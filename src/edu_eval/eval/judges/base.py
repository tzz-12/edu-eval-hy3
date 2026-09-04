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

    # 双采样视角扰动指令（原型用）：同一 Judge 两次采样用不同审视视角制造真实
    # 分歧。因指令进入 user_prompt，cache_key 自动换键、两次采样不串味。
    # 主流程不传 context["_lens"]，故默认返回空串、对现有评测零副作用。
    _LENS_DIRECTIVES: Dict[str, str] = {
        "strict_rubric": (
            "【本次采样审视视角】请严格按上方量规逐条逐项核对，只依据显式原文证据打分，"
            "不做发散推断，不确定即标记 ne。"
        ),
        "learner_view": (
            "【本次采样审视视角】请切换到「学习者 / 同行教师」的真实课堂视角审视该教学设计："
            "设想学生实际学习与教师使用场景，重点判断各维度在真实教学中的达成度与体验，"
            "再回到量规给分。"
        ),
    }

    def _lens_directive(self, context: Dict[str, Any]) -> str:
        lens = (context or {}).get("_lens")
        if not lens:
            return ""
        directive = self._LENS_DIRECTIVES.get(lens)
        if not directive:
            return ""
        return directive + "\n\n"

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

    @staticmethod
    def _normalize_score_keys(scores: Dict[str, Any],
                              valid_ids: set) -> Dict[str, Any]:
        """scores 键归一化（与 normalize 同规则），供「校验」与「归一化」共用。

        模型偶发把键写成「维度 1」「G0」等形式；校验阶段若不做同样的归一化，
        会把「内容正确但键名不规范」的输出误判成维度缺失而白白重试。
        """
        fixed: Dict[str, Any] = {}
        for k, v in scores.items():
            kk = str(k).replace("维度", "").strip()
            fixed[kk if kk in valid_ids else k] = v
        return fixed

    def _schema_valid(self, data: Dict[str, Any]) -> bool:
        """多维度输出的**结构**校验：scores 必须是 dict、覆盖本角色全部维度，
        且每个维度要么 ne=True、要么 score 为 1–5 整数。

        为什么必须有这道校验（实测事故）：hy3 偶发把「多维度评判」退化成
        「单维度结构」——顶层给出 score/evidence/ne，同时多出一个**非 dict 的
        scores 字段**（如整数 1）。此前基类 _output_valid 恒返回 True，这类畸形
        输出被判为有效 → 不重试、且**写进缓存造成污染**；下游 normalize 才发现
        scores 非 dict 并把它清空，最终表现为「该 Judge 所有维度分数为 null」，
        被误读成「模型拒答」。校验前置后，畸形输出会走重试，拿不到才判无效。
        """
        from edu_eval.eval import dimensions as _D

        required = _D.JUDGE_GROUPS.get(self.role)
        if not required:
            # 非多维度角色（如层内采样仲裁员）不套用本校验
            return True
        scores = data.get("scores")
        if not isinstance(scores, dict):
            return False
        valid_ids = {d.id for d in _D.DIMENSIONS}
        fixed = self._normalize_score_keys(scores, valid_ids)
        for did in required:
            item = fixed.get(did)
            if not isinstance(item, dict):
                return False
            if item.get("ne"):
                continue  # ne 是合法判定，不要求 score
            s = item.get("score")
            if not (isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5):
                return False
        return True

    def _output_valid(self, data: Dict[str, Any]) -> bool:
        """输出有效性钩子：默认执行结构校验。

        子类可覆写并先调 super()._output_valid(data) 叠加角色专属规则
        （如 FactJudge 的「自报 PASS 必须有维度 2 判定」）。

        无效输出与解析失败同等对待：自动重试一次，且绝不写缓存
        （否则坏结果会被同键复用）。
        """
        return self._schema_valid(data)

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
        # 与 _schema_valid 共用同一套键归一化规则，避免两处规则漂移
        data["scores"] = BaseJudge._normalize_score_keys(scores, valid_ids)
        data.setdefault("scores", {})
        data.setdefault("suggestions", [])
        data.setdefault("admission", "PASS")
        data.setdefault("redline", False)
        return data
