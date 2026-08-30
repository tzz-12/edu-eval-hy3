"""Judge 基类：角色提示构建、JSON 解析兜底、缓存集成。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

from edu_eval.hy3 import Hy3Client
from edu_eval.eval.cache import PROMPT_VERSION, JudgeCache, cache_key


class BaseJudge:
    role: str = "base"
    system: str = ""

    def __init__(self, client: Hy3Client, cache: JudgeCache | None = None):
        self.client = client
        self.cache = cache

    # 子类覆写：构建 user prompt
    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "") -> str:
        raise NotImplementedError

    def run(self, text: str, context: Dict[str, Any],
            kb_context: str = "") -> Dict[str, Any]:
        """执行判定（带缓存与 JSON 解析兜底）。"""
        key = cache_key(text, self.role, self.client.cfg.temperature,
                        self.client.cfg.model)
        if self.cache and self.cache.get(key):
            return self.cache.get(key)

        user = self.build_user_prompt(text, context, kb_context)
        raw = self.client.judge(self.system, user)
        data = self._parse_json(raw)
        data["_meta"] = {"role": self.role, "prompt_version": PROMPT_VERSION}

        if self.cache:
            self.cache.put(key, data)
        return data

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        """严格解析 → 花括号抽取兜底 → 空结果。"""
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {}

    @staticmethod
    def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
        """补齐缺省字段。"""
        data.setdefault("scores", {})
        data.setdefault("suggestions", [])
        data.setdefault("admission", "PASS")
        data.setdefault("redline", False)
        return data
