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
                          kb_context: str = "", rule_evidence: str = "") -> str:
        raise NotImplementedError

    def run(self, text: str, context: Dict[str, Any],
            kb_context: str = "", rule_evidence: str = "") -> Dict[str, Any]:
        """执行判定（带缓存与 JSON 解析兜底）。

        rule_evidence：规则层（零 LLM）的确定性判定，作为硬证据注入提示。

        缓存键对**渲染后的完整提示**取哈希（P0-10 · E）：先建提示再查缓存，
        这样 kb_context / rule_evidence / context 中任何一项变化都会自动换键，
        不会因新增输入维度而漏键串味。
        """
        user = self.build_user_prompt(text, context, kb_context, rule_evidence)
        if self.cache:
            key = cache_key(self.role, self.client.cfg.temperature,
                            self.client.cfg.model, self.system, user, context)
            cached = self.cache.get(key)
            if cached is not None:
                return cached
        else:
            key = None

        raw = self.client.judge(self.system, user)
        data = self._parse_json(raw)
        data["_meta"] = {"role": self.role, "prompt_version": PROMPT_VERSION}

        if self.cache and key:
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
