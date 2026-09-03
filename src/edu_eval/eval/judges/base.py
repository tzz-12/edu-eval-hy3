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

    @staticmethod
    def _competency_block(context: Dict[str, Any], dim_id: str) -> str:
        """取该维度的「课标核心素养依据」块（编排层注入，无则空串）。

        评规各维度的锚点已写入素养定语，但 Judge 手里若没有课标原文，
        就只能凭模型记忆猜「这算不算培养抽象能力」——不可核验。
        这里把 [KB#cmp-xxx] 原文挂到对应维度下，让判定有据可引。
        """
        blocks = (context or {}).get("_competency") or {}
        body = blocks.get(str(dim_id))
        if not body:
            return ""
        return (
            "\n课标核心素养依据（判定「素养导向」时优先引用这些条目，"
            "在 evidence 中用 [KB#cmp-xxx] 标注编号）：\n" + body
        )

    def _output_valid(self, data: Dict[str, Any]) -> bool:
        """输出有效性钩子：子类按角色校验必需字段，默认视为有效。

        无效输出与解析失败同等对待：自动重试一次，且绝不写缓存
        （否则坏结果会被同键复用）。
        """
        return True

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
        if data.get("_parse_failed") or not self._output_valid(data):
            # 真实 hy3 实测偶发两类坏输出：畸形 JSON（scores 为数字等）、
            # 以及「自报 PASS 却缺关键判定字段」（FactJudge 缺维度 2）。
            # 自动重试一次：单次调用失败不该拖垮整份报告。
            raw = self.client.judge(self.system, user)
            data = self._parse_json(raw)
        data["_meta"] = {"role": self.role, "prompt_version": PROMPT_VERSION}

        # 解析失败/无效输出的结果绝不写缓存：否则坏结果会被同键复用
        # （实测踩坑：旧缓存把空解析结果当成有效判定反复命中）
        if (self.cache and key and not data.get("_parse_failed")
                and self._output_valid(data)):
            self.cache.put(key, data)
        return data

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        """严格解析 → 花括号抽取兜底 → 诚实失败。

        解析失败时绝不能返回空 dict：normalize 会把空 dict 兜底成
        admission=PASS / scores={}，即「模型没评上分却被当成通过」。
        这里显式返回 NE + _parse_failed 标记，由编排层留痕告警。
        """
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
        return {
            "admission": "NE", "redline": False, "scores": {},
            "suggestions": [], "_parse_failed": True,
            "_raw_excerpt": raw[:300],
        }

    @staticmethod
    def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
        """补齐缺省字段 + scores 键归一化。

        真实 hy3 实测发现：模型偶发把 scores 键写成 \"维度 1\"、\"G0\" 等
        形式而非维度 id（\"1\"、\"2\"），导致聚合时全部按未知维度丢弃、
        weight_coverage 骤降。提示词已强化约束，此处再做程序级修正兜底。
        """
        from edu_eval.eval import dimensions as _D

        valid_ids = {d.id for d in _D.DIMENSIONS}
        scores = data.get("scores")
        if scores is not None and not isinstance(scores, dict):
            # 实测 hy3 偶发把 scores 返回成数字/字符串等非 dict 形态，
            # 直接进编排层会在 scores.update() 处崩溃 → 归为解析失败并留痕
            data["_parse_failed"] = True
            data.setdefault("_raw_excerpt", f"scores 字段非 dict：{scores!r}"[:200])
            scores = {}
        else:
            scores = scores or {}
        fixed: Dict[str, Any] = {}
        for k, v in scores.items():
            kk = str(k).replace("维度", "").strip()
            fixed[kk if kk in valid_ids else k] = v
        data["scores"] = fixed
        data.setdefault("scores", {})
        data.setdefault("suggestions", [])
        data.setdefault("admission", "PASS")
        data.setdefault("redline", False)
        return data
