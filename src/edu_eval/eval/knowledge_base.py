"""本地知识库：用于知识正确性准入校验（G0）。

知识条目来自课程标准和开放授权教育资源，每条必须保留来源、许可、版本与适用条件。
来源冲突的条目进入隔离区，不参与在线核验。
本模块提供轻量检索（基于词元重叠），无需额外依赖；生产环境可替换为 SQLite FTS5。

P0-10 · A 修复
--------------
早期实现的 `KBEntry` 只认 Tier 2 字段（topic/formula/conditions…），
而 `data/kb/knowledge.jsonl` 是 Tier 1 概念索引（name/grade/aliases…）。
`KnowledgeBase.load()` 因此必抛 `TypeError`，而调用方 `load_kb_assets()`
用 `except Exception: kb = None` 把异常**静默吞掉** —— 结果是 fact Judge
拿到的知识库上下文恒为空字符串、`kb_hits` 恒为 0，界面上完全看不出来。

修复要点：
1. 按 `tier` 字段分派到 `schema.Tier1Entry` / `Tier2Entry`，用各自的
   `from_dict()`（过滤未知键）做宽容装载，两层 schema 不再互相打架；
2. 装载失败**抛错而非返回空**，由调用方决定降级并显式记录告警；
3. `retrieve()` 不再接受「整篇文档」当查询（词元集合过大 → 任何条目都命中，
   top_k 实为随机）。改用 `retrieve_for_text()`：先抽概念，再按概念查条目。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..kb.schema import Tier1Entry, Tier2Entry

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")

#: 长文本直接当查询会导致词元集合爆炸（见模块 docstring），超过该长度即拒绝
_MAX_QUERY_CHARS = 60


@dataclass
class KBEntry:
    """统一的检索条目视图（抹平 Tier1 / Tier2 字段差异）。"""

    id: str
    topic: str
    definition: str = ""
    formula: str = ""
    conditions: str = ""
    common_errors: str = ""
    source: str = ""
    license: str = ""
    version: str = ""
    applicable_to: str = ""
    quarantined: bool = False  # 来源冲突隔离区
    tier: int = 1
    grade: str = ""
    aliases: List[str] = field(default_factory=list)

    @property
    def text_blob(self) -> str:
        return " ".join(
            x for x in (self.topic, " ".join(self.aliases), self.definition,
                        self.formula, self.conditions, self.common_errors,
                        self.applicable_to) if x
        )

    @property
    def names(self) -> List[str]:
        return [self.topic] + list(self.aliases)


class KnowledgeBase:
    def __init__(self, entries: List[KBEntry]):
        self.entries = entries
        self._by_id: Dict[str, KBEntry] = {e.id: e for e in entries}

    # ---------------------------------------------------------------- 装载
    @classmethod
    def load(cls, path: str) -> "KnowledgeBase":
        """装载 JSONL 知识库。

        按每条的 `tier` 字段分派 schema：Tier 1 概念索引 / Tier 2 可核验断言。
        **失败时抛出**，不做静默降级 —— 静默降级是 P0 阶段最难发现的一类缺陷。
        """
        entries: List[KBEntry] = []
        errors: List[str] = []
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    errors.append(f"{path}:{lineno} JSON 解析失败：{e}")
                    continue
                try:
                    entries.append(_adapt(obj))
                except (TypeError, ValueError) as e:
                    errors.append(f"{path}:{lineno} 条目字段不兼容：{e}")
        if errors and not entries:
            raise KnowledgeBaseError(
                f"知识库 {path} 装载失败（0 条可用，{len(errors)} 条错误）："
                + "；".join(errors[:3])
            )
        return cls(entries)

    @classmethod
    def load_safe(cls, path: str) -> Tuple[Optional["KnowledgeBase"], List[str]]:
        """带告警的装载：绝不静默失败，把问题返回给调用方展示。"""
        if not os.path.exists(path):
            return None, [f"知识库文件不存在：{path}（请先运行 "
                          f"`python -m edu_eval.kb.ingest` 重建）"]
        try:
            kb = cls.load(path)
        except (OSError, KnowledgeBaseError, json.JSONDecodeError) as e:
            return None, [f"知识库装载失败：{e}"]
        warns = []
        if not kb.entries:
            warns.append(f"知识库 {path} 为空")
        return kb, warns

    # ---------------------------------------------------------------- 检索
    def get(self, entry_id: str) -> Optional[KBEntry]:
        return self._by_id.get(entry_id)

    def retrieve(self, query: str, top_k: int = 5) -> List[KBEntry]:
        """返回与查询最相关的若干个非隔离条目（按词元重叠打分）。

        查询应为**概念名或短语**，不接受整篇文档：文档词元集合过大时
        几乎所有条目都命中，top_k 实际退化为随机抽样。
        """
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []
        scored = []
        for e in self.entries:
            if e.quarantined:
                continue
            e_tokens = set(_tokenize(e.text_blob))
            if not e_tokens:
                continue
            overlap = len(q_tokens & e_tokens)
            if overlap:
                # 名称命中优先于正文命中：概念名短，纯词元重叠会被长定义淹没
                name_bonus = 5 if any(n in query for n in e.names if n) else 0
                scored.append((overlap + name_bonus, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def retrieve_for_text(self, text: str, top_k: int = 5,
                          concept_names: Optional[List[str]] = None) -> List[KBEntry]:
        """从整篇教学设计文本中检索知识条目。

        策略：显式概念名优先（由调用方从检索器给出更好），否则退回用
        文档中出现的**概念名**逐个查表 —— 而不是拿整篇文档当查询。
        """
        if concept_names:
            found: Dict[str, KBEntry] = {}
            leftovers: List[str] = []
            for name in concept_names:
                hit = self._lookup_by_name(name)
                if hit:
                    found[hit.id] = hit
                else:
                    leftovers.append(name)
            if len(found) >= top_k:
                return list(found.values())[:top_k]
            for name in leftovers:
                for e in self.retrieve(name, top_k=2):
                    found.setdefault(e.id, e)
                if len(found) >= top_k:
                    break
            return list(found.values())[:top_k]

        # 无概念名可用：在文档中查找知识条目名称的出现（长名优先）
        hits: Dict[str, KBEntry] = {}
        candidates = sorted(
            (e for e in self.entries if not e.quarantined),
            key=lambda e: -max((len(n) for n in e.names), default=0),
        )
        for e in candidates:
            if any(n and len(n) >= 2 and n in text for n in e.names):
                hits[e.id] = e
                if len(hits) >= top_k:
                    break
        return list(hits.values())

    def _lookup_by_name(self, name: str) -> Optional[KBEntry]:
        for e in self.entries:
            if e.topic == name:
                return e
        for e in self.entries:
            if name in e.aliases:
                return e
        return None

    # ---------------------------------------------------------------- 输出
    def format_for_prompt(self, entries: List[KBEntry]) -> str:
        if not entries:
            return "（知识库未检索到相关条目；请基于通用数学原则与可核验推导判断）"
        lines = []
        for e in entries:
            body = e.definition or e.formula
            meta = f"Tier{e.tier}"
            if e.grade:
                meta += f" ｜ 年级:{e.grade}"
            lines.append(
                f"[KB#{e.id} | 主题:{e.topic} | {meta} | 来源:{e.source} | 许可:{e.license}]\n"
                f"定义/公式：{body}\n"
                f"适用条件：{e.conditions}\n常见错误：{e.common_errors}"
            )
        return "\n\n".join(lines)


class KnowledgeBaseError(RuntimeError):
    """知识库装载失败（不再静默降级）。"""


def _adapt(obj: dict) -> KBEntry:
    """把原始 JSON 条目适配为统一视图（按 tier 分派 schema）。"""
    tier = int(obj.get("tier", 1) or 1)
    if tier == 2:
        raw = Tier2Entry.from_dict(obj)
        return KBEntry(
            id=raw.id, topic=raw.topic, definition=raw.assertion,
            formula=raw.formula, conditions=raw.conditions,
            common_errors="；".join(raw.common_errors or []),
            source=raw.source, license=raw.license, version=raw.version,
            applicable_to=raw.grade,
            quarantined=raw.quarantined or not raw.is_verified,
            tier=2, grade=raw.grade,
        )
    raw1 = Tier1Entry.from_dict(obj)
    return KBEntry(
        id=raw1.id, topic=raw1.name, definition=raw1.definition,
        formula=raw1.formula,
        conditions=(f"教材定位：{raw1.grade}" if raw1.grade else ""),
        common_errors="",
        source=raw1.source, license=raw1.license, version=raw1.version,
        applicable_to=raw1.grade,
        quarantined=bool(raw1.quarantined),
        tier=1, grade=raw1.grade, aliases=list(raw1.aliases or []),
    )


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())
