"""本地知识库：用于知识正确性准入校验（G0）。

知识条目来自课程标准和开放授权教育资源，每条必须保留来源、许可、版本与适用条件。
来源冲突的条目进入隔离区，不参与在线核验。
本模块提供轻量检索（基于词元重叠），无需额外依赖；生产环境可替换为 SQLite FTS5。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]")


@dataclass
class KBEntry:
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

    @property
    def text_blob(self) -> str:
        return " ".join(
            x for x in (self.topic, self.definition, self.formula,
                        self.conditions, self.common_errors, self.applicable_to) if x
        )


class KnowledgeBase:
    def __init__(self, entries: List[KBEntry]):
        self.entries = entries

    @classmethod
    def load(cls, path: str) -> "KnowledgeBase":
        entries: List[KBEntry] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                entries.append(KBEntry(**obj))
        return cls(entries)

    def retrieve(self, query: str, top_k: int = 5) -> List[KBEntry]:
        """返回与查询最相关的若干个非隔离条目（按词元重叠打分）。"""
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
                scored.append((overlap, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def format_for_prompt(self, entries: List[KBEntry]) -> str:
        if not entries:
            return "（知识库未检索到相关条目；请基于通用数学原则与可核验推导判断）"
        lines = []
        for e in entries:
            lines.append(
                f"[KB#{e.id} | 主题:{e.topic} | 来源:{e.source} | 许可:{e.license}]\n"
                f"定义/公式：{e.definition or e.formula}\n"
                f"适用条件：{e.conditions}\n常见错误：{e.common_errors}"
            )
        return "\n\n".join(lines)


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())
