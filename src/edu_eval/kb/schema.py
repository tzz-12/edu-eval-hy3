"""知识库条目 schema：Tier 1 概念索引 / Tier 2 可核验断言。

设计依据 IMPLEMENTATION_PLAN.md §3.1：

- Tier 1（概念索引）：来自 K12-KGraph 的 451 条初中概念，用于检索召回、
  维度 3 超纲判断（确定性查表）、维度 7 前置知识。
- Tier 2（可核验断言）：13 课题 × 8–15 条核心断言，用于 G0 知识准入。
  仅 `verification.status == "verified"` 的条目参与 G0 判定；
  `unverified` 一律返回 NE（无法判定），不参与判定。

所有条目强制携带来源元数据（source / license / version），
`quarantined=True` 的条目（来源冲突）不参与检索与 G0。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

#: 参与检索与 G0 判定的前提：来源元数据三件套
REQUIRED_META = ("source", "license", "version")

#: 年级册次合法值（人教版初中）
VALID_GRADES = (
    "七年级上册", "七年级下册", "八年级上册", "八年级下册",
    "九年级上册", "九年级下册",
)


@dataclass
class Tier1Entry:
    """Tier 1 概念索引条目（K12-KGraph 导入）。"""

    id: str = ""
    name: str = ""
    tier: int = 1
    definition: str = ""
    importance: str = ""          # 了解 / 理解 / 掌握 / 运用
    grade: str = ""               # 合法值见 VALID_GRADES
    publisher: str = "人教版"
    aliases: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)  # Tier1 条目 id
    related: List[str] = field(default_factory=list)        # Tier1 条目 id
    formula: str = ""             # K12-KGraph 原生填充率 12.9%，缺失允许为空
    examples: List[str] = field(default_factory=list)
    source: str = ""
    license: str = ""
    version: str = ""
    std_ref: Optional[str] = None  # 课标条目引用（后续 P0-4 回填）
    quarantined: bool = False

    def validate(self) -> List[str]:
        """返回错误列表；空列表表示通过校验。"""
        errors: List[str] = []
        if not self.id:
            errors.append("Tier1 条目缺少 id")
        if not self.name:
            errors.append(f"{self.id}: 缺少 name")
        if not self.definition:
            errors.append(f"{self.id}: 缺少 definition")
        if self.grade:
            # 概念可跨册复现（如「科学计数法」在七上与八上均出现），用「、」分隔
            for part in self.grade.split("、"):
                if part not in VALID_GRADES:
                    errors.append(f"{self.id}: 年级 {part!r} 不在合法集合")
        for m in REQUIRED_META:
            if not getattr(self, m):
                errors.append(f"{self.id}: 缺少来源元数据 {m}")
        return errors

    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "Tier1Entry":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in obj.items() if k in known})


@dataclass
class Tier2Entry:
    """Tier 2 可核验断言条目（G0 知识准入专用）。"""

    id: str = ""                  # 如 as-0701-003（课题号-序号）
    topic: str = ""                # 13 课题之一，见 docs/kb_scope.md
    tier: int = 2
    grade: str = ""               # 如 "七年级"（学段级，可跨册次）
    assertion: str = ""            # 自然语言断言（可核验）
    formula: str = ""              # sympy 可解析表达式（可为空）
    conditions: str = ""          # 适用条件，如 "a ≠ 0"
    common_errors: List[str] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    #   method:  sympy_solve / sympy_simplify / manual / none
    #   status:  verified / unverified / quarantined
    #   checked_at: ISO 时间戳
    std_ref: str = ""              # 课标依据，如 "课标·第四学段【内容要求】…"
    source: str = ""
    license: str = ""
    version: str = ""
    quarantined: bool = False

    @property
    def is_verified(self) -> bool:
        return (
            not self.quarantined
            and self.verification.get("status") == "verified"
        )

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.id:
            errors.append("Tier2 条目缺少 id")
        if not self.topic:
            errors.append(f"{self.id}: 缺少 topic")
        if not self.assertion:
            errors.append(f"{self.id}: 缺少 assertion")
        if self.verification and self.verification.get("status") not in (
            "verified", "unverified", "quarantined"
        ):
            errors.append(f"{self.id}: verification.status 非法")
        for m in REQUIRED_META:
            if not getattr(self, m):
                errors.append(f"{self.id}: 缺少来源元数据 {m}")
        return errors

    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "Tier2Entry":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in obj.items() if k in known})


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def dump_jsonl(entries: List[Any], path: str) -> None:
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")


def validate_entries(raw: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """批量校验 JSONL 原始条目，返回 {条目id: [错误…]}（仅含有错的条目）。"""
    problems: Dict[str, List[str]] = {}
    for obj in raw:
        tier = obj.get("tier", 1)
        entry = (Tier2Entry if tier == 2 else Tier1Entry).from_dict(obj)
        errs = entry.validate()
        if errs:
            problems[entry.id or "<missing-id>"] = errs
    return problems
