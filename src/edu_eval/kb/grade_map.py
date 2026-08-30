"""概念 → 年级册次确定性映射（维度 3 超纲判断的数据基础）。

产出 data/kb/concept_grade.json：
  {concept_id: {"name", "grades": [...], "chapters": [...], "books": [...]}}

运行时 API（零 LLM 调用、零幻觉）：
  GradeMap.get(concept_id)            → 映射记录（或 None）
  GradeMap.within(concept_id, grade)   → 概念是否属于声明年级范围
  GradeMap.analyze(ids, declared)     → 超纲分析报告（维度 3 硬证据）

语义约定：
- 概念可跨册复现（如勾股定理 {八下, 九下}）。判断规则为
  「概念册次集合 ∩ 声明允许册次集合 ≠ ∅」→ 在范围内。
  复现即复习可用，符合教学实践；首次定义册次天然包含于集合。
- 未知概念（不在 Tier 1 中）不猜测：返回 unknown，由上层
  决定交由 LLM 或标记 NE。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Set

GRADE_ORDER = [
    "七年级上册", "七年级下册", "八年级上册", "八年级下册",
    "九年级上册", "九年级下册",
]
GRADE_LEVEL = {g: i // 2 + 7 for i, g in enumerate(GRADE_ORDER)}  # → 7/8/9

# 声明年级归一化：接受「七年级 / 初一 / 七上 / 7年级上册…」等写法
_DECL_PATTERNS = [
    (re.compile(r"(七|7|初\s*一)"), ["七年级上册", "七年级下册"]),
    (re.compile(r"(八|8|初\s*二)"), ["八年级上册", "八年级下册"]),
    (re.compile(r"(九|9|初\s*三)"), ["九年级上册", "九年级下册"]),
]
_HALF_PATTERNS = [
    (re.compile(r"上\s*(册|学期)"), "上"),
    (re.compile(r"下\s*(册|学期)"), "下"),
]


def normalize_declared_grade(declared: str) -> Set[str]:
    """把用户声明的年级归一化为允许册次集合。

    返回空集合表示无法解析（上层应返回 NE，不得猜测）。
    """
    if not declared:
        return set()
    text = declared.strip().replace("年级", "").replace("初", "")
    grades: Set[str] = set()
    for pat, gs in _DECL_PATTERNS:
        if pat.search(declared) or pat.search(text):
            grades.update(gs)
    if not grades:
        return set()
    # 细化到单册：声明了「上册/下册」
    half = None
    for pat, h in _HALF_PATTERNS:
        if pat.search(declared):
            half = h
            break
    if half:
        grades = {g for g in grades if g.endswith(h + "册")}
    return grades


class GradeMap:
    def __init__(self, mapping: Dict[str, dict]):
        self.mapping = mapping

    @classmethod
    def load(cls, path: str = "data/kb/concept_grade.json") -> "GradeMap":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def get(self, concept_id: str) -> Optional[dict]:
        return self.mapping.get(concept_id)

    def grades_of(self, concept_id: str) -> List[str]:
        rec = self.mapping.get(concept_id)
        return list(rec["grades"]) if rec else []

    def within(self, concept_id: str, declared_grade: str) -> Optional[bool]:
        """概念是否在声明年级范围内；未知概念返回 None。"""
        rec = self.mapping.get(concept_id)
        if not rec:
            return None
        allowed = normalize_declared_grade(declared_grade)
        if not allowed:
            return None  # 声明不可解析 → 不猜测
        return bool(set(rec["grades"]) & allowed)

    def analyze(self, concept_ids: List[str], declared_grade: str) -> dict:
        """维度 3 超纲分析：确定性、可追溯（每条结论附概念名与所属册次）。"""
        allowed = normalize_declared_grade(declared_grade)
        result = {
            "declared": declared_grade,
            "allowed": sorted(allowed),
            "parse_ok": bool(allowed),
            "total": len(concept_ids),
            "known": 0, "unknown": 0,
            "within": [], "beyond": [],
        }
        if not allowed:
            result["error"] = "声明年级无法解析，维度 3 返回 NE"
            return result
        for cid in concept_ids:
            rec = self.mapping.get(cid)
            if not rec:
                result["unknown"] += 1
                continue
            result["known"] += 1
            item = {"id": cid, "name": rec["name"], "grades": rec["grades"]}
            if set(rec["grades"]) & allowed:
                result["within"].append(item)
            else:
                item["min_grade"] = min(rec["grades"], key=GRADE_ORDER.index)
                result["beyond"].append(item)
        return result


def build(raw_path: str, jsonl_path: str, out_path: str) -> dict:
    """从 Tier 1 JSONL + 原始图谱（章节信息）构建映射表。"""
    sys_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(sys_dir, os.pardir, raw_path) if not os.path.exists(raw_path) else raw_path
    with open(raw_path, encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g["nodes"], g["edges"]
    node_by_id = {n["id"]: n for n in nodes}

    # 概念 → Section（appears_in）
    appears: Dict[str, Set[str]] = {}
    for e in edges:
        if e["type"] != "appears_in":
            continue
        if "target" in e:
            appears.setdefault(e["source"], set()).add(e["target"])

    # Section/Chapter → Book（is_part_of 向上）
    parent = {e["source"]: e["target"] for e in edges
              if e["type"] == "is_part_of" and "target" in e}

    def book_of(node_id: str) -> Optional[str]:
        cur = node_id
        for _ in range(3):
            cur = parent.get(cur)
            if cur is None:
                return None
            if cur.startswith("math_") and cur.endswith("_rjb") and cur.count("_") == 3:
                return cur
        return None

    mapping: Dict[str, dict] = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            books, chapters = set(), set()
            for sec in appears.get(obj["id"], ()):
                chapters.add(sec)
                b = book_of(sec)
                if b:
                    books.add(b)
            mapping[obj["id"]] = {
                "name": obj["name"],
                "grades": sorted(obj["grade"].split("、")) if obj["grade"] else [],
                "chapters": sorted(chapters),
                "books": sorted(books),
            }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=1)
    return mapping


if __name__ == "__main__":
    # 在仓库根目录运行：python -m edu_eval.kb.grade_map
    mapping = build("data/raw/k12_math.json",
                    "data/kb/knowledge.jsonl",
                    "data/kb/concept_grade.json")

    mapped = sum(1 for v in mapping.values() if v["grades"])
    print(f"映射表：{len(mapping)} 概念，其中年级可映射 {mapped} "
          f"({mapped / len(mapping) * 100:.1f}%)")
    from collections import Counter
    cnt = Counter(g for v in mapping.values() for g in v["grades"])
    for g in GRADE_ORDER:
        print(f"  {g}: {cnt.get(g, 0)}")

    # 性能与语义自测
    out = "data/kb/concept_grade.json"
    gm = GradeMap.load(out)
    t0 = time.perf_counter()
    for _ in range(1000):
        gm.within("math_9b_rjb_cpt1", "七年级")  # 任意 id
    dt = (time.perf_counter() - t0) / 1000 * 1000
    print(f"单条查询耗时：{dt:.3f} ms（要求 <10ms）")

    # 超纲语义抽查：一元二次方程（九上）出现在七年级设计中 → 超纲
    x = next(cid for cid, v in mapping.items() if v["name"] == "一元二次方程")
    assert gm.within(x, "九年级") is True
    assert gm.within(x, "七年级") is False
    # 勾股定理跨册复现 {八下,九下}：八年级 / 九年级均不超纲
    gpt = next(cid for cid, v in mapping.items() if v["name"] == "勾股定理")
    assert gm.within(gpt, "八年级") is True and gm.within(gpt, "九年级") is True
    assert gm.within(gpt, "七年级") is False
    # 声明归一化
    assert normalize_declared_grade("初一") == {"七年级上册", "七年级下册"}
    assert normalize_declared_grade("8年级上册") == {"八年级上册"}
    assert normalize_declared_grade("高二") == set()
    print("语义自测全部通过")
