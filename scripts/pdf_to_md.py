#!/usr/bin/env python3
"""PDF 教学设计 → 清洗版 Markdown（校准样本降噪）。

处理三类抽取噪声：
1. 私有区符号字符（Symbol 字体 \\uf0XX → ASCII/常用符号）
2. 页码页眉（"1 / 5"）
3. 上标断裂（sort 模式下 x² 读成 "x 2"）

用法: python scripts/pdf_to_md.py <src_dir> <dst_dir>
"""
import re
import sys
from pathlib import Path

import pymupdf

SUPERSCRIPTS = {'2': '²', '3': '³'}


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


def strip_orphan_superscript(line: str) -> str:
    """删除行首游离的上标数字（sort 模式把 (x-h)² 的 2 排到了行首）。

    仅当行内含公式上下文（'y =' 或 '函数'）时执行，避免误删正文编号。"""
    if not re.search(r'y\s*=|函数', line):
        return line
    return re.sub(r'^(?:\s*[23]\s{2,})+', '', line)


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
        line = strip_orphan_superscript(line)
        lines.append(line)
    return '\n'.join(lines).strip() + '\n'


def convert(pdf_path: Path, dst_dir: Path) -> Path:
    doc = pymupdf.open(pdf_path)
    parts = []
    for page in doc:
        parts.append(page.get_text(sort=True))
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
