#!/usr/bin/env python3
"""PDF 教学设计 → 清洗版 Markdown（校准样本降噪）。

处理三类抽取噪声：
1. 私有区符号字符（Symbol 字体 \\uf0XX → ASCII/常用符号）
2. 页码页眉（"1 / 5"）
3. 上标还原：该 PDF 的上标是普通 '2'/'3' 字形（字号更小、位置上移），
   并非真正的 ² 字符；旧脚本用 get_text(sort=True) 把上标排到行首，
   再被 strip_orphan_superscript 删除 → ² 系统性丢失且同文档不一致。
   现改用 bbox 感知抽取（convert_page_dict）：按字形位置聚类成行、按 x
   排序，把上标数字还原为 ²/³ 并合并到前一个 token，从源头消除丢失。

用法: python scripts/pdf_to_md.py <src_dir> <dst_dir>
"""
import re
import sys
from pathlib import Path

import pymupdf

SUPERSCRIPTS = {'2': '²', '3': '³', '1': '¹', '0': '⁰', 'n': 'ⁿ', 'i': 'ⁱ'}


def map_private(c: str) -> str:
    r"""Symbol 字体私有区 \uf0XX → 对应 ASCII 字符。"""
    o = ord(c)
    if 0xF000 <= o <= 0xF0FF:
        return chr(o - 0xF000)
    return c


def fix_superscripts(line: str) -> str:
    r"""把数学上下文中孤立的上标指数修复为 ²/³。

    仅在行内含 '='（公式行）时应用，避免误伤编号/年份。
    模式: ") 2 +" -> ")² +" ; "ax 2" -> "ax²" ; "( x - h) 2" -> "( x - h)²"
    """
    if '=' not in line:
        return line
    # 字母或右括号后跟独立数字 2/3，且后面是空格+运算符/标点/汉字/行尾
    line = re.sub(r'([a-zA-Z\)])\s+([23])(?=\s*(?:[+\-—，。；、:：）)\]]|[\u4e00-\u9fff]|$))',
                  lambda m: m.group(1) + SUPERSCRIPTS.get(m.group(2), m.group(2)),
                  line)
    return line


def clean(text: str) -> str:
    text = ''.join(map_private(c) for c in text)
    lines = []
    for raw in text.split('\n'):
        line = raw.rstrip()
        # 页码行: "1 / 5"
        if re.fullmatch(r'\s*\d+\s*/\s*\d+\s*', line):
            continue
        # 纯空行折叠标记
        if not line.strip():
            if lines and lines[-1] == '':
                continue
            lines.append('')
            continue
        line = fix_superscripts(line)
        # 注意：不再调用 strip_orphan_superscript —— 旧逻辑会把 'sort' 模式推到
        # 行首的孤立上标数字删除，造成 ² 永久丢失。上标现由 convert_page_dict
        # 在抽取阶段直接还原为 ²/³，无需事后删除。
        lines.append(line)
    return '\n'.join(lines).strip() + '\n'


def _cluster_lines(spans: list) -> list:
    """按字形 bbox 自己聚类成行（PyMuPDF 对逐字 run 的默认分行会把公式打散）。

    spans: 已含 x0/y0/x1/y1/cx/cy/size/text 的字典列表。返回行的列表，
    每行是其中的 span 子列表（按 x0 升序）。
    """
    if not spans:
        return []
    # 按 y0（顶部）排序，从上到下贪心聚类
    spans = sorted(spans, key=lambda s: (s["y0"], s["x0"]))
    lines = []
    cur = [spans[0]]
    cur_bottom = spans[0]["y1"]
    cur_dom = spans[0]["size"]
    for s in spans[1:]:
        # 同一行：该字形顶部不高于当前行底部太多（容差取行字号的 0.35 倍，
        # 过宽会把相邻视觉行错误合并）
        tol = max(1.0, 0.35 * cur_dom)
        if s["y0"] <= cur_bottom + tol:
            cur.append(s)
            cur_bottom = max(cur_bottom, s["y1"])
            cur_dom = max(cur_dom, s["size"])
        else:
            lines.append(cur)
            cur = [s]
            cur_bottom = s["y1"]
            cur_dom = s["size"]
    lines.append(cur)
    return lines


def convert_page_dict(page) -> str:
    """bbox 感知抽取：按字形位置聚类成行、按 x 排序，上标数字还原为 ²/³ 并
    合并到前一个 token。解决该 PDF 逐字 run + 上标字形被拆散/误删的问题。"""
    d = page.get_text("dict")
    spans = []
    for blk in d["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            for sp in line["spans"]:
                t = (sp.get("text") or "").strip()
                if not t:
                    continue
                x0, y0, x1, y1 = sp["bbox"]
                spans.append({
                    "text": t,
                    "size": sp.get("size", 0.0),
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                })
    out_lines = []
    for ln in _cluster_lines(spans):
        ln.sort(key=lambda s: s["x0"])
        dom = max(s["size"] for s in ln)
        line_cy = sum(s["cy"] for s in ln) / len(ln)
        parts = []
        prev = None
        for s in ln:
            is_sup = (s["size"] < 0.85 * dom) or (s["cy"] < line_cy - 1.5)
            if is_sup and s["text"].isdigit():
                # 上标数字：合并到前一个 token（或作为行首上标单独保留）
                sup = "".join(SUPERSCRIPTS.get(c, c) for c in s["text"])
                if parts:
                    parts[-1] += sup
                else:
                    parts.append(sup)
            else:
                # 与上一个 token 存在明显水平间隙则补空格（恢复词间空白）
                if prev is not None and s["x0"] - prev["x1"] > 0.3 * dom:
                    parts.append(" ")
                parts.append(s["text"])
                prev = s
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def convert(pdf_path: Path, dst_dir: Path) -> Path:
    doc = pymupdf.open(pdf_path)
    parts = [convert_page_dict(page) for page in doc]
    text = clean('\n'.join(parts))
    out = dst_dir / (pdf_path.stem + '.md')
    out.write_text(text, encoding='utf-8')
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(src.glob('*.pdf')):
        out = convert(pdf, dst)
        n = len(out.read_text(encoding='utf-8'))
        print(f'{pdf.name} -> {out.name} ({n} chars)')


if __name__ == '__main__':
    main()
