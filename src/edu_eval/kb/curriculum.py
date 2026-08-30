"""课标 2022（初中第四学段）OCR 文本 → 结构化 JSONL。

从 data/raw/curriculum_junior.txt 解析出：
  {domain, section, topic, subtopic, item_no, page, text, std_id}

- domain  : （一）数与代数 … （四）综合与实践
- section : 内容要求 / 学业要求 / 教学提示
- topic   : 1. 数与式 / 2. 方程与不等式 / …
- subtopic: （1） 有理数 / （2） 实数 / …
- item_no : 内容要求下的 ①②③ 条目号（其他栏为 None）

产出 data/kb/curriculum_junior.jsonl，供 Tier 2 断言的 std_ref
与维度 7 / 维度 9 的课标依据引用。

已知限制（docs/sources_inventory.md）：OCR 公式失真，公式不取自本文本。
"""
from __future__ import annotations

import argparse
import json
import os
import re

DOMAIN_RE = re.compile(r"^（[一二三四五]）(\S+)")
TOPIC_RE = re.compile(r"^(\d)\.\s*(.+)$")
SUBTOPIC_RE = re.compile(r"^（(\d)）\s*(.+)$")
ITEM_RE = re.compile(r"^([①②③④⑤⑥⑦⑧⑨⑩])\s*(.+)$", re.S)
SECTION_KEYS = ("内容要求", "学业要求", "教学提示")


def _clean_line(line: str) -> str:
    """去掉页眉与页码噪声。"""
    s = line.strip()
    if not s:
        return ""
    # 页眉形如「四、课程内容|0」「I 义务教育 数学 课程标准（2022年版）」
    if re.match(r"^[IVX]+\s*义务教育", s) or re.match(r"^四、课程内容", s):
        return ""
    if re.match(r"^义务教育\s*$", s):
        return ""
    return s


def split_pages(text: str):
    """按页切分，返回 [(页码, 该页文本), ...]。"""
    parts = re.split(r"===== \[印刷第(\d+)页\] =====", text)
    pages = []
    for i in range(1, len(parts), 2):
        pages.append((int(parts[i]), parts[i + 1]))
    return pages


def parse(text: str):
    pages = split_pages(text)
    # 行级处理：每行携带所属页码（首行 = 记录起始页）
    lines: list = []  # [(页码, 行文本), ...]
    for page_no, chunk in pages:
        for raw in chunk.split("\n"):
            lines.append((page_no, raw))

    # 跳过头部：从「（一）数与代数」开始
    start_idx = next(i for i, (_, ln) in enumerate(lines) if DOMAIN_RE.match(ln.strip()))
    lines = lines[start_idx:]

    records = []
    domain = topic = subtopic = None
    section = None
    item_buf: list = []
    item_no = None
    item_page = None

    def flush():
        nonlocal item_buf, item_no, item_page
        if section and item_buf:
            body = "".join(item_buf).strip()
            if body:
                records.append({
                    "domain": domain, "section": section,
                    "topic": topic, "subtopic": subtopic,
                    "item_no": item_no, "text": body,
                    "page": item_page,
                })
        item_buf, item_no, item_page = [], None, None

    for page_no, raw in lines:
        line = _clean_line(raw)
        if not line:
            continue
        # 去掉 OCR 换行词间粘连的空格（中文内不应有半角空格）
        compact = re.sub(r"(?<=[\u4e00-\u9fff，。；：（）])\s+(?=[\u4e00-\u9fff，。；：（）])", "", line)

        m = DOMAIN_RE.match(compact)
        if m:
            flush()
            domain, section = m.group(1), None
            topic = subtopic = None
            continue

        if compact.startswith("【"):
            flush()
            section = next((k for k in SECTION_KEYS if k in compact), None)
            topic = subtopic = None
            continue

        m = TOPIC_RE.match(compact)
        if m and section:
            flush()
            topic, subtopic = m.group(2).strip(), None
            item_buf, item_page = [compact[m.end():]], page_no
            continue

        m = SUBTOPIC_RE.match(compact)
        if m and section:
            flush()
            subtopic = m.group(2).strip()
            item_buf, item_page = [compact[m.end():]], page_no
            continue

        m = ITEM_RE.match(compact)
        if m and section == "内容要求":
            flush()
            item_no = m.group(1)
            item_buf, item_page = [m.group(2)], page_no
            continue

        if section:
            if not item_buf:
                item_page = page_no
            item_buf.append(compact)

    flush()
    return records


def build(raw_path: str, out_path: str) -> list:
    with open(raw_path, encoding="utf-8") as f:
        text = f.read()
    records = parse(text)

    # 附页码与 std_id
    for i, r in enumerate(records):
        r["std_id"] = f"cur-{i + 1:03d}"
        r["source"] = "curriculum-2022"
        r["license"] = "政府公开文件（引用注明出处）"
        r["version"] = "2022 年版"

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return records


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/curriculum_junior.txt")
    ap.add_argument("--out", default="data/kb/curriculum_junior.jsonl")
    args = ap.parse_args()
    records = build(args.raw, args.out)

    from collections import Counter
    print(f"解析条目：{len(records)} → {args.out}")
    print("按领域:", Counter(r["domain"] for r in records))
    print("按栏位:", Counter(r["section"] for r in records))
    print("\n样例（内容要求·有理数）:")
    for r in records:
        if r["section"] == "内容要求" and r["subtopic"] == "有理数" and r["item_no"]:
            print(" ", r["item_no"], r["text"][:60])
