"""多格式文件解析：Markdown / TXT / DOCX / PDF / PPTX → 结构化文本。

解析层只负责把不同格式统一抽取为可评估的正文文本与简单结构信息，
不判断内容质量。PDF 与 PPTX 依赖第三方库；若解析失败会标记 parse_status=partial。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class ParsedDoc:
    text: str
    structure: List[str] = field(default_factory=list)  # 章节标题列表
    parse_status: str = "ok"          # ok / partial
    parse_confidence: float = 0.95
    notes: str = ""


def parse_file(path: str) -> ParsedDoc:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt"):
        return _parse_text(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".pptx":
        return _parse_pptx(path)
    raise ValueError(f"不支持的文件格式：{ext}（支持 .md/.txt/.docx/.pdf/.pptx）")


def _split_sections(text: str) -> List[str]:
    heads = re.findall(r"^#{1,6}\s+(.+)$|^(第?[一二三四五六七八九十\d]+[、.．]\s*.+)$", text, re.M)
    return [h[0] or h[1] for h in heads if (h[0] or h[1])]


def _parse_text(path: str) -> ParsedDoc:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return ParsedDoc(text=text, structure=_split_sections(text), parse_confidence=0.98)


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
    return ParsedDoc(text="\n".join(parts), structure=heads, parse_confidence=0.95)


def _parse_pdf(path: str) -> ParsedDoc:
    try:
        import pdfplumber
    except ImportError:
        return ParsedDoc(text="", parse_status="partial", parse_confidence=0.0,
                         notes="未安装 pdfplumber，无法解析 .pdf。请 pip install pdfplumber。")
    try:
        with pdfplumber.open(path) as pdf:
            pages = [pg.extract_text() or "" for pg in pdf.pages]
        text = "\n".join(pages)
        return ParsedDoc(text=text, structure=[f"第{i+1}页" for i in range(len(pages))],
                         parse_confidence=0.9 if text.strip() else 0.3,
                         notes="" if text.strip() else "PDF 未抽取到文本（可能为图片型）。")
    except Exception as exc:  # noqa: BLE001
        return ParsedDoc(text="", parse_status="partial", parse_confidence=0.0,
                         notes=f"PDF 解析失败：{exc}")


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
        slides_text.append(f"[幻灯片 {i}]\n" + "\n".join(buf))
    text = "\n\n".join(slides_text)
    return ParsedDoc(text=text, structure=[f"幻灯片 {i+1}" for i in range(len(prs.slides))],
                     parse_confidence=0.9)
