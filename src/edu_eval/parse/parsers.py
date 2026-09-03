"""多格式文件解析：Markdown / TXT / DOCX / PDF / PPTX → 结构化文本。

解析层只负责把不同格式统一抽取为可评估的正文文本与简单结构信息，
不判断内容质量。

P0-6 升级：
- PDF 优先用 PyMuPDF（fitz），pdfplumber 兜底；空文本扫描件返回
  parse_status="ocr_required"（明确状态，而非含糊的 partial）。
- 全格式输出页序（pages）、章节层级（structure）、公式标记（formulas）。
- 置信度按格式与抽取量分级，供上层决定是否阻断（G0 解析不可靠 → NE）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List

from edu_eval.parse.layout import (
    build_outline,
    denoise_text,
    extract_formula_marks,
)


@dataclass
class ParsedDoc:
    text: str
    structure: List[str] = field(default_factory=list)   # 章节标题列表
    pages: List[str] = field(default_factory=list)       # 按页/幻灯片切分的文本
    formulas: List[str] = field(default_factory=list)    # 公式标记
    parse_status: str = "ok"          # ok / partial / ocr_required
    parse_confidence: float = 0.95
    notes: str = ""

    def finalize(self) -> "ParsedDoc":
        """统一补算结构字段（各格式解析器填充 text/pages 后调用）。"""
        self.formulas = extract_formula_marks(self.text)
        if not self.structure:
            self.structure = [t for _, t in build_outline(self.text)]
        return self


def parse_file(path: str, denoise: bool = False) -> ParsedDoc:
    """解析文档。`denoise` 对文本类输入（.md/.txt）额外跑一遍抽取降噪。

    PDF 抽取的噪声（词内断裂、栏位空格）会污染维度 5 与维度 A：实测人教社
    示范课例在这两维恒被扣到 3 分，证据却全是"教学过 程"这类抽取瑕疵。
    因此 **PDF 路径默认降噪**；.md/.txt 被视为"已清洗"输入，默认不动，
    由调用方（CLI `--denoise`）对「PDF 转存而来的文本」显式开启。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt"):
        return _parse_text(path, denoise=denoise)
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path, denoise=True)
    if ext == ".pptx":
        return _parse_pptx(path)
    raise ValueError(f"不支持的文件格式：{ext}（支持 .md/.txt/.docx/.pdf/.pptx）")


def _parse_text(path: str, denoise: bool = False) -> ParsedDoc:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if denoise:
        text = denoise_text(text)
    return ParsedDoc(text=text, parse_confidence=0.98).finalize()


def _parse_docx(path: str) -> ParsedDoc:
    try:
        from docx import Document
    except ImportError:
        return ParsedDoc(text="", parse_status="partial", parse_confidence=0.0,
                         notes="未安装 python-docx，无法解析 .docx。请 pip install python-docx。")
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    heads = [p.text.strip() for p in doc.paragraphs
             if p.style.name.startswith("Heading") and p.text.strip()]
    # DOCX 无固定分页概念，按大节近似分页
    pages = _approx_pages(parts)
    return ParsedDoc(text="\n".join(parts), structure=heads, pages=pages,
                     parse_confidence=0.95).finalize()


def _approx_pages(parts: List[str], per_page: int = 25) -> List[str]:
    pages = []
    for i in range(0, len(parts), per_page):
        pages.append("\n".join(parts[i:i + per_page]))
    return pages or [""]


def _parse_pdf(path: str, denoise: bool = True) -> ParsedDoc:
    pages = None
    notes = []
    # 1) PyMuPDF（优先）
    try:
        try:
            import pymupdf as pdfmod  # 新 API
        except ImportError:  # 兼容旧版本
            import fitz as pdfmod
        with pdfmod.open(path) as pdf:
            pages = [pg.get_text("text") or "" for pg in pdf]
        if any(p.strip() for p in pages):
            raw = "\n".join(pages)
            text = denoise_text(raw) if denoise else raw
            return ParsedDoc(
                text=text,
                structure=[f"第{i+1}页" for i in range(len(pages))],
                pages=pages, parse_confidence=0.92,
            ).finalize()
    except ImportError:
        notes.append("未安装 PyMuPDF，尝试 pdfplumber 兜底")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"PyMuPDF 解析失败：{exc}，尝试 pdfplumber 兜底")

    # 2) pdfplumber 兜底
    if pages is None or not any(p.strip() for p in pages):
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                pages = [pg.extract_text() or "" for pg in pdf.pages]
        except ImportError:
            return ParsedDoc(
                text="", parse_status="partial", parse_confidence=0.0,
                notes="未安装 PyMuPDF / pdfplumber，无法解析 .pdf。",
            )
        except Exception as exc:  # noqa: BLE001
            return ParsedDoc(
                text="", parse_status="partial", parse_confidence=0.0,
                notes=f"PDF 解析失败：{exc}",
            )

    raw = "\n".join(pages)
    text = denoise_text(raw) if denoise else raw
    if not text.strip():
        # 空文本 → 图片型扫描件，明确状态（G0 将返回 NE）
        return ParsedDoc(
            text="", pages=pages,
            structure=[f"第{i+1}页" for i in range(len(pages))],
            parse_status="ocr_required", parse_confidence=0.1,
            notes="PDF 未抽取到文本：图片型扫描件，需要 OCR 后再评估。",
        )
    return ParsedDoc(
        text=text, pages=pages,
        structure=[f"第{i+1}页" for i in range(len(pages))],
        parse_confidence=0.88, notes="; ".join(notes),
    ).finalize()


def _parse_pptx(path: str) -> ParsedDoc:
    try:
        from pptx import Presentation
    except ImportError:
        return ParsedDoc(text="", parse_status="partial", parse_confidence=0.0,
                         notes="未安装 python-pptx，无法解析 .pptx。请 pip install python-pptx。")
    prs = Presentation(path)
    slides_text = []
    for i, slide in enumerate(prs.slides, 1):
        buf = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                buf.append(shape.text_frame.text)
        # 备注页文本（教师讲稿，对评估很重要）
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            note = slide.notes_slide.notes_text_frame.text.strip()
            if note:
                buf.append(f"[备注] {note}")
        slides_text.append("\n".join(buf))
    pages = [f"[幻灯片 {i+1}]\n{t}" for i, t in enumerate(slides_text)]
    return ParsedDoc(
        text="\n\n".join(pages),
        structure=[f"幻灯片 {i+1}" for i in range(len(slides_text))],
        pages=pages, parse_confidence=0.9,
    ).finalize()
