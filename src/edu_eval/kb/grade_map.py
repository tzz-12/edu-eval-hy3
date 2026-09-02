"""概念 → 年级册次确定性映射（维度 3 超纲判断的数据基础）。

产出 data/kb/concept_grade.json：
  {concept_id: {"name", "grades": [...], "chapters": [...], "books": [...]}}

运行时 API（零 LLM 调用、零幻觉）：
  GradeMap.get(concept_id)            → 映射记录（或 None）
  GradeMap.within(concept_id, grade)   → 概念是否属于声明年级范围
  GradeMap.analyze(ids, declared)     → 超纲分析报告（维度 3 硬证据）

语义约定（P0-10 · D 修正）：
- **超纲 ≠ 不在本册**。概念按册次**先后序号**比较，而不是集合求交：
  取概念的**最早引入册次** `earliest` 与声明范围的**最晚册次** `latest`：
    * `earliest <= latest` → 已引入（在范围内）
    * `earliest >  latest` → 真超纲（尚未学到）
- 修正前的实现用「概念册次集合 ∩ 声明册次集合 ≠ ∅」判断，把**早于**
  声明年级的前置知识（九年级教案引用「有理数」）也判成了超纲：
  声明年级越高，误判越多（九年级 319/451 = 71%），与真实超纲风险恰好相反。
- 已引入的概念再细分为两类，供维度 3 / 维度 7 引用：
    * `current` —— 落在声明册次范围内（本册新授或复现）
    * `earlier` —— 早于声明册次范围（**前置知识/已学内容，属正常引用**）
- 概念可跨册复现（如勾股定理 {八下, 九下}）：复现即复习可用，
  首次定义册次天然是 `earliest`。
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
GRADE_INDEX = {g: i for i, g in enumerate(GRADE_ORDER)}           # 册次先后序号 0..5


def grade_ordinal(grade: str) -> Optional[int]:
    """册次在学程中的先后序号；非法册次返回 None（不猜测、不上取整）。"""
    return GRADE_INDEX.get(grade)


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
    def load(cls, path: Optional[str] = None) -> "GradeMap":
        if path is None:
            from edu_eval import paths as P  # 延迟导入：默认路径不依赖 cwd

            path = P.grade_json()
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def get(self, concept_id: str) -> Optional[dict]:
        return self.mapping.get(concept_id)

    def grades_of(self, concept_id: str) -> List[str]:
        rec = self.mapping.get(concept_id)
        return list(rec["grades"]) if rec else []

    def classify(self, concept_id: str, declared_grade: str) -> Optional[str]:
        """判定单个概念相对声明年级的位置。

        返回：
          "current"  落在声明册次范围内（本册新授或复现）
          "earlier"  早于声明范围 —— 前置知识 / 已学内容，**正常引用，不算超纲**
          "beyond"   晚于声明范围 —— 真超纲（尚未学到）
          None       未知概念，或声明年级不可解析（不猜测）
        """
        rec = self.mapping.get(concept_id)
        if not rec:
            return None
        allowed = normalize_declared_grade(declared_grade)
        if not allowed:
            return None  # 声明不可解析 → 不猜测
        latest = max(GRADE_INDEX[g] for g in allowed)
        ordinals = [GRADE_INDEX[g] for g in rec["grades"] if g in GRADE_INDEX]
        if not ordinals:
            return None  # 概念册次全部非法 → 不猜测
        earliest = min(ordinals)
        if earliest > latest:
            return "beyond"
        return "current" if max(ordinals) >= min(GRADE_INDEX[g] for g in allowed) else "earlier"

    def within(self, concept_id: str, declared_grade: str) -> Optional[bool]:
        """概念在声明年级节点是否**已经引入**（不晚于声明）。

        注意语义：返回 True 包含「本册内容」与「前置知识」两种情形。
        需要区分二者请使用 classify()。未知概念 / 声明不可解析返回 None。
        """
        kind = self.classify(concept_id, declared_grade)
        if kind is None:
            return None
        return kind != "beyond"

    def analyze(self, concept_ids: List[str], declared_grade: str) -> dict:
        """维度 3 超纲分析：确定性、可追溯（每条结论附概念名与所属册次）。

        返回键：
          within  已引入（= current + earlier，向后兼容旧调用方）
          current 本册范围内
          earlier 前置知识（早于声明范围，**不得据此判超纲**）
          beyond  真超纲（概念最早引入册次晚于声明最晚册次）
        """
        allowed = normalize_declared_grade(declared_grade)
        result = {
            "declared": declared_grade,
            "allowed": sorted(allowed),
            "parse_ok": bool(allowed),
            "total": len(concept_ids),
            "known": 0, "unknown": 0,
            "within": [], "current": [], "earlier": [], "beyond": [],
        }
        if not allowed:
            result["error"] = "声明年级无法解析，维度 3 返回 NE"
            return result
        earliest_allowed = min(GRADE_INDEX[g] for g in allowed)
        latest_allowed = max(GRADE_INDEX[g] for g in allowed)
        for cid in concept_ids:
            rec = self.mapping.get(cid)
            if not rec:
                result["unknown"] += 1
                continue
            result["known"] += 1
            ordinals = [GRADE_INDEX[g] for g in rec["grades"] if g in GRADE_INDEX]
            item = {
                "id": cid, "name": rec["name"], "grades": rec["grades"],
                "earliest_grade": (GRADE_ORDER[min(ordinals)] if ordinals else ""),
            }
            kind = self.classify(cid, declared_grade)
            if kind is None:
                # 概念册次全部非法：无法判定，计入 unknown 而**不**计入超纲
                result["unknown"] += 1
                result["known"] -= 1
                continue
            result["within"].append(item)
            if kind == "beyond":
                result["beyond"].append(item)
            elif kind == "current":
                result["current"].append(item)
            else:
                result["earlier"].append(item)
        result["allowed_range"] = [GRADE_ORDER[earliest_allowed],
                                   GRADE_ORDER[latest_allowed]]
        return result


def build(raw_path: str, jsonl_path: str, out_path: str) -> dict:
    """从 Tier 1 JSONL + 原始图谱（章节信息）构建映射表。"""
    from edu_eval import paths as P  # 延迟导入：避免与 paths 的循环依赖

    raw_path = P.resolve(raw_path)
    jsonl_path = P.resolve(jsonl_path)
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
    from edu_eval import paths as P

    # 任意目录运行均可：python -m edu_eval.kb.grade_map
    mapping = build(P.resolve(os.path.join("data", "raw", "k12_math.json")),
                    P.kb_jsonl(),
                    P.grade_json())

    mapped = sum(1 for v in mapping.values() if v["grades"])
    print(f"映射表：{len(mapping)} 概念，其中年级可映射 {mapped} "
          f"({mapped / len(mapping) * 100:.1f}%)")
    from collections import Counter
    cnt = Counter(g for v in mapping.values() for g in v["grades"])
    for g in GRADE_ORDER:
        print(f"  {g}: {cnt.get(g, 0)}")

    # 性能与语义自测
    out = P.grade_json()
    gm = GradeMap.load(out)
    t0 = time.perf_counter()
    for _ in range(1000):
        gm.within("math_9b_rjb_cpt1", "七年级")  # 任意 id
    dt = (time.perf_counter() - t0) / 1000 * 1000
    print(f"单条查询耗时：{dt:.3f} ms（要求 <10ms）")

    def cid_of(name: str) -> str:
        return next(cid for cid, v in mapping.items() if v["name"] == name)

    # --- 超纲方向：概念晚于声明 ---
    x = cid_of("一元二次方程")                       # 九上
    assert gm.within(x, "九年级") is True
    assert gm.within(x, "七年级") is False
    assert gm.classify(x, "七年级") == "beyond"
    # 勾股定理跨册复现 {八下,九下}：八年级 / 九年级均不超纲，七年级超纲
    gpt = cid_of("勾股定理")
    assert gm.within(gpt, "八年级") is True and gm.within(gpt, "九年级") is True
    assert gm.within(gpt, "七年级") is False

    # --- 反向：概念早于声明 = 前置知识，不得判超纲（P0-10 · D 回归）---
    yls = cid_of("有理数")                           # 七上
    assert gm.classify(yls, "九年级") == "earlier", "前置知识被误判为超纲"
    assert gm.within(yls, "九年级") is True
    assert gm.classify(yls, "七年级") == "current"
    # 九年级声明下，七~八年级概念一个都不该进 beyond
    rep9 = gm.analyze(list(mapping), "九年级")
    earlier_names = {i["name"] for i in rep9["earlier"]}
    beyond_names = {i["name"] for i in rep9["beyond"]}
    assert not (earlier_names & beyond_names), "同一概念同时进 earlier 与 beyond"
    assert "有理数" not in beyond_names and "有理数" in earlier_names
    # 单调性：声明年级越高，被判超纲的概念只能越少（不能变多）
    counts = [len(gm.analyze(list(mapping), g)["beyond"])
              for g in ("七年级", "八年级", "九年级")]
    assert counts[0] >= counts[1] >= counts[2], f"超纲数随年级升高而增加：{counts}"
    print(f"  超纲概念数 七/八/九年级：{counts}（应单调不增）")

    # 声明归一化
    assert normalize_declared_grade("初一") == {"七年级上册", "七年级下册"}
    assert normalize_declared_grade("8年级上册") == {"八年级上册"}
    assert normalize_declared_grade("高二") == set()
    print("语义自测全部通过")
