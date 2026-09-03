"""课标 2022「核心素养与学段目标」条目：整理稿 → JSONL → 装载检索。

与 `curriculum.py`（160 条内容要求）并列，但覆盖的是课标的**目标侧**：

- 核心素养内涵（三会：数学眼光 / 数学思维 / 数学语言）
- 小学与初中阶段的核心素养主要表现（初中 9 项）
- 素养 → 三会归属（含学段目标与学业质量描述中的官方证据句）
- 第四学段（7~9年级）学段目标
- 学业质量标准（评估三个方面 + 第四学段学业质量描述）

用途：为维度 1（目标与课标对齐）、维度 3（学段适配）、维度 7（学情）、
维度 9（启发探究）提供**可直接引用页码/章节的官方依据**，
使「教学目标是否体现核心素养导向」成为可核验判定，而非印象分。

来源与约束（`kb/sources.yaml` 的 `curriculum-2022`）：
- 政府公开文件，逐字录入并注明出处；
- 公式一律不录入（转录不可靠），仅保留语义内容；
- `derived=true` 条目为课标原文片段的拼接/归纳，非单一自然段原文。

产出：data/kb/curriculum_competency.jsonl（gitignore，由脚本一键重建）
输入：data/curriculum/curriculum_competency.md（人工整理稿，**入库**以便审计）
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

#: 元数据行：@ k=v | k=v | …
HEADER_RE = re.compile(r"^@\s*(.+)$")
#: 允许「|」出现在 locator 文本中？不允许，故按「|」切分是安全的
KV_RE = re.compile(r"^([A-Za-z_]+)\s*=\s*(.*)$")

BOOL_TRUE = {"true", "1", "yes", "y"}


@dataclass
class CompetencyEntry:
    """课标核心素养 / 学段目标 / 学业质量条目。"""

    std_id: str = ""              # cmp-001 …
    category: str = ""            # 核心素养内涵 / 学段主要表现 / 素养—三会归属 / 学段目标 / 学业质量标准
    aspect: str = ""              # 数学眼光 / 数学思维 / 数学语言（可空）
    competency: str = ""          # 抽象能力 …（可空）
    section: str = ""             # 课程目标 / 学业质量
    topic: str = ""               # （一）核心素养内涵 …
    subtopic: str = ""            # 1. 核心素养的构成 …
    item_no: str = ""             # （1）/ 小学 / 初中 …
    text: str = ""                # 课标原文或拼接原文
    page: Optional[int] = None    # 印刷页码；不确定时为 None（用 locator 定位）
    locator: str = ""             # 章节定位串（必须）
    stage: str = ""               # 义务教育 / 小学阶段 / 第四学段（7~9年级）
    dimensions: List[str] = field(default_factory=list)
    derived: bool = False         # 非单段原文（拼接/归纳）
    ocr_corrected: bool = False   # 取自 OCR 并已订正错字
    source: str = "curriculum-2022"
    license: str = "政府公开文件（引用注明出处）"
    version: str = "2022 年版"
    provenance: str = (
        "教育部《义务教育数学课程标准（2022 年版）》"
        "·三、课程目标 / 五、学业质量"
    )

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.std_id:
            errors.append("条目缺少 std_id")
        if not self.category:
            errors.append(f"{self.std_id}: 缺少 category")
        if not self.text.strip():
            errors.append(f"{self.std_id}: 缺少 text")
        if not self.locator.strip():
            errors.append(f"{self.std_id}: 缺少 locator（章节定位）")
        if self.page is not None and self.page <= 0:
            errors.append(f"{self.std_id}: page 非正整数")
        for m in ("source", "license", "version"):
            if not getattr(self, m):
                errors.append(f"{self.std_id}: 缺少来源元数据 {m}")
        return errors

    @classmethod
    def from_dict(cls, obj: Dict) -> "CompetencyEntry":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in obj.items() if k in known})


def _resolve(rel: str) -> str:
    """按项目约定解析路径（延迟导入，避免与 paths 形成循环依赖）。"""
    from edu_eval import paths  # 同 kb/retriever.py、kb/grade_map.py 的约定
    return paths.resolve(rel)


def _parse_header(line: str) -> Dict[str, str]:
    """解析 `@ k=v | k=v` 元数据行。"""
    out: Dict[str, str] = {}
    for part in line.split("|"):
        m = KV_RE.match(part.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def parse_markdown(md_text: str) -> List[Dict]:
    """把整理稿解析为条目字典列表（尚未分配 std_id）。"""
    records: List[Dict] = []
    meta: Optional[Dict[str, str]] = None
    body: List[str] = []

    def flush() -> None:
        nonlocal meta, body
        if meta:
            text = "\n".join(body).strip()
            if not text:
                # 写了元数据行却漏了正文：静默丢弃会让录入错误隐形，必须报错
                raise ValueError(
                    f"核心素养整理稿存在无正文的记录："
                    f"category={meta.get('category', '')!r} "
                    f"locator={meta.get('locator', '')!r}"
                )
            rec = dict(meta)
            rec["text"] = text
            records.append(rec)
        meta, body = None, []

    in_fence = False                      # ``` 代码块内的示例不得被当成记录
    for raw in md_text.splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HEADER_RE.match(s):
            flush()
            meta = _parse_header(HEADER_RE.match(s).group(1))
            continue
        if meta is None:
            continue                      # 头部说明区，跳过
        if not s:
            continue
        if s.startswith("#") or s.startswith(">") or s.startswith("---"):
            continue                      # 说明性标题/引用/分隔线，不进正文
        body.append(s)
    flush()

    entries: List[Dict] = []
    for i, rec in enumerate(records, start=1):
        page_raw = (rec.get("page") or "").strip()
        entry = CompetencyEntry(
            std_id=f"cmp-{i:03d}",
            category=rec.get("category", ""),
            aspect=rec.get("aspect", ""),
            competency=rec.get("competency", ""),
            section=rec.get("section", ""),
            topic=rec.get("topic", ""),
            subtopic=rec.get("subtopic", ""),
            item_no=rec.get("item_no", ""),
            text=rec.get("text", ""),
            page=int(page_raw) if page_raw.isdigit() else None,
            locator=rec.get("locator", ""),
            stage=rec.get("stage", ""),
            dimensions=[d for d in (rec.get("dims", "") or "").split(",") if d],
            derived=(rec.get("derived", "") or "").lower() in BOOL_TRUE,
            ocr_corrected=(rec.get("ocr_corrected", "") or "").lower() in BOOL_TRUE,
        )
        entries.append(asdict(entry))
    return entries


def build(raw_path: Optional[str] = None, out_path: Optional[str] = None) -> List[Dict]:
    """整理稿 → JSONL；任一条目校验失败即抛错（不静默降级）。"""
    from edu_eval import paths  # 延迟导入，见 _resolve 注释
    raw = raw_path or paths.resolve(paths.COMPETENCY_MD)
    out = out_path or paths.resolve(paths.COMPETENCY_JSONL)
    with open(raw, encoding="utf-8") as f:
        entries = parse_markdown(f.read())

    problems: Dict[str, List[str]] = {}
    for obj in entries:
        errs = CompetencyEntry.from_dict(obj).validate()
        if errs:
            problems[obj.get("std_id", "<missing>")] = errs
    if problems:
        raise ValueError(f"核心素养条目校验失败：{problems}")

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return entries


# --------------------------------------------------------------------------
# 装载与检索
# --------------------------------------------------------------------------

def load_competencies(path: Optional[str] = None) -> List[CompetencyEntry]:
    """装载条目；文件缺失时抛 FileNotFoundError（由调用方决定是否降级）。"""
    from edu_eval import paths  # 延迟导入，见 _resolve 注释
    p = path or paths.resolve(paths.COMPETENCY_JSONL)
    with open(p, encoding="utf-8") as f:
        return [CompetencyEntry.from_dict(json.loads(ln))
                for ln in f if ln.strip()]


def by_category(entries: Iterable[CompetencyEntry], category: str) -> List[CompetencyEntry]:
    return [e for e in entries if e.category == category]


def by_dimension(entries: Iterable[CompetencyEntry], dimension: str) -> List[CompetencyEntry]:
    """按评测维度召回（dims 字段标注，如 "1" / "3" / "9"）。"""
    d = str(dimension)
    return [e for e in entries if d in e.dimensions]


def by_competency(entries: Iterable[CompetencyEntry], name: str) -> List[CompetencyEntry]:
    """按素养名召回（如「抽象能力」），含正文命中。"""
    hits = [e for e in entries if e.competency == name]
    if hits:
        return hits
    return [e for e in entries if name and name in e.text]


def search(entries: Iterable[CompetencyEntry], query: str, limit: int = 5) -> List[CompetencyEntry]:
    """朴素关键词检索：按字段加权计分，无第三方依赖。

    权重：competency/aspect 精确命中 5 分，category 3 分，subtopic 2 分，
    正文出现 1 分。用于 Judge 提示中的依据召回，不追求 BM25 精度。
    """
    q = (query or "").strip()
    if not q:
        return []
    scored = []
    for e in entries:
        score = 0
        if q in e.competency:
            score += 5
        if q in e.aspect:
            score += 5
        if q in e.category:
            score += 3
        if q in e.subtopic:
            score += 2
        if q in e.text:
            score += 1
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: (-x[0], x[1].std_id))
    return [e for _, e in scored[:limit]]


def format_for_prompt(entries: Iterable[CompetencyEntry], limit: int = 6) -> str:
    """渲染为 Judge 提示中的「课标依据」文本块（带 locator，便于引用）。"""
    lines = []
    for e in list(entries)[:limit]:
        loc = f"（{e.locator}）" if e.locator else ""
        body = e.text if len(e.text) <= 220 else e.text[:217] + "…"
        tag = e.competency or e.aspect or e.category
        lines.append(f"[KB#{e.std_id}] {tag}{loc}：{body}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 按评测维度召回（Judge 提示注入用）
# --------------------------------------------------------------------------

#: 单个维度注入的条目上限。hy3 是推理模型，reasoning_tokens 与输出共享
#: max_tokens 预算（实测常顶格 6.5k/8k），素养块必须与正文、量规争同一个
#: 上下文窗口，因此必须限量，不能把 30 条全量塞进提示。
PER_DIM_LIMIT = 3

#: 提示中单条正文的截断长度（课标原文单条最长约 260 字）
PROMPT_TEXT_LIMIT = 160

#: 召回优先级：越靠前的类别越权威、越可直接引用。
#: 「核心素养内涵」是课标官方定义（判定素养导向的第一依据）；
#: 「学段主要表现」界定初中阶段考察范围；「素养—三会归属」为跨段拼接
#: （derived=true），优先级低于前两者。
CATEGORY_PRIORITY = {
    "核心素养内涵": 0,
    "学段主要表现": 1,
    "素养—三会归属": 2,
    "学段目标": 3,
    "学业质量标准": 4,
}


def _rank(entries: List[CompetencyEntry], focus: Optional[List[str]],
          limit: int) -> List[CompetencyEntry]:
    """排序取前 `limit` 条：命中 focus 的优先，其次按类别权威度，最后按 id 稳定。

    `focus` 取维度的 `competency_link`（评规声明本维度重点考察的素养名）。
    命中 focus 的条目排在前面，名额不足时才用其余条目补齐 ——
    否则「维度 9 重点考察创新意识」却召回到运算能力，Judge 会被带偏。
    三级排序保证同一输入永远得到同一输出（缓存可复现）。
    """
    focus_set = set(focus or ())
    return sorted(
        entries,
        key=lambda e: (
            0 if (focus_set and (e.competency in focus_set or e.aspect in focus_set)) else 1,
            CATEGORY_PRIORITY.get(e.category, 9),
            e.std_id,
        ),
    )[:limit]


def by_dimension_ranked(entries: Iterable[CompetencyEntry], dimension: str,
                        focus: Optional[List[str]] = None,
                        limit: int = PER_DIM_LIMIT) -> List[CompetencyEntry]:
    """按维度召回（dims 字段）并排序，取前 `limit` 条。"""
    return _rank(by_dimension(entries, dimension), focus, limit)


def format_for_dimension(entries: Iterable[CompetencyEntry],
                         focus: Optional[List[str]] = None,
                         limit: int = PER_DIM_LIMIT) -> str:
    """渲染「课标核心素养依据」块（Judge 提示注入用）。

    条目带 [KB#cmp-xxx] 编号与 locator，Judge 引用即可追溯到课标章节，
    使「目标有没有素养导向」成为可核验判定而非印象分。
    """
    lines = []
    for e in _rank(list(entries), focus, limit):
        body = e.text if len(e.text) <= PROMPT_TEXT_LIMIT \
            else e.text[: PROMPT_TEXT_LIMIT - 1] + "…"
        tag = e.competency or e.aspect or e.category
        lines.append(f"[KB#{e.std_id}] {tag}（{e.locator}）：{body}")
    return "\n".join(lines)


if __name__ == "__main__":
    from collections import Counter

    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    args = ap.parse_args()

    from edu_eval import paths  # 延迟导入，见 _resolve 注释
    raw = args.raw or paths.resolve(paths.COMPETENCY_MD)
    with open(raw, encoding="utf-8") as f:
        entries = parse_markdown(f.read())
    if args.check:
        bad = {}
        for obj in entries:
            errs = CompetencyEntry.from_dict(obj).validate()
            if errs:
                bad[obj["std_id"]] = errs
        print(f"校验 {len(entries)} 条，问题 {len(bad)} 条")
        for k, v in bad.items():
            print(" ", k, v)
        raise SystemExit(1 if bad else 0)

    out = args.out or paths.resolve(paths.COMPETENCY_JSONL)
    n = build(args.raw, out)
    print(f"解析条目：{len(n)} → {out}")
    print("按类别:", Counter(e["category"] for e in n))
    print("按学段:", Counter(e["stage"] for e in n))
    print("按维度:", Counter(d for e in n for d in e["dimensions"]))
