"""Hy3 客户端封装（OpenAI 兼容接口）。

所有模型能力均通过 Hy3 完成，本项目不训练或微调任何模型。
API Key、Base URL、模型名仅来自环境变量（见 config.Hy3Config）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import Hy3Config

_SYSTEM_PREAMBLE = (
    "你是 EduEval 的教育评测裁判（Judge）。你只依据给定的量规、用户声明的教学目标与"
    "原文证据进行评分，不得凭借外部知识臆测。必须严格返回 JSON，不要输出任何额外说明文字。"
)


class QuotaExceededError(RuntimeError):
    """额度耗尽 / 持续限流：全局故障，重试无意义，必须中止整份评测。

    与「单个 Judge 调用失败」有本质区别：后者可重试、重试不成该维度降级 NE，
    评测的其他部分仍然有效；前者意味着**后续所有调用都会失败**，若继续按
    「降级 NE」处理，会产出一份所有维度都是 NE、看起来正常实则完全无效的
    报告——这才是最危险的失败模式（实测事故见下）。
    """


#: 连续 429 达到该次数即判定为额度耗尽（而非瞬时抖动）并熔断。
#: 取 3 而非 1 是为了容忍真实的瞬时速率限制（per-minute），
#: 但足以在额度耗尽时尽早止损：实测一次无效全量跑会浪费 128 次调用。
RATE_LIMIT_ABORT_AFTER = 3


class Hy3Client:
    def __init__(self, cfg: Hy3Config):
        self.cfg = cfg
        self._client = None
        #: 连续 429 计数（成功即清零），用于区分瞬时抖动与额度耗尽
        self._consecutive_rate_limits = 0
        if not cfg.mock:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("未安装 openai 库，请先 `pip install openai`。") from exc
            if not cfg.base_url or not cfg.api_key:
                raise RuntimeError("HY3_BASE_URL 与 HY3_API_KEY 均不可为空。")
            self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key,
                                  timeout=cfg.timeout)

    def _note_rate_limit(self, exc: BaseException) -> None:
        """记录 429；连续达到阈值即熔断（抛 QuotaExceededError 中止整份评测）。

        为什么不用「遇到 429 就中止」：真实存在 per-minute 的瞬时速率限制，
        偶发 1–2 次属于可恢复抖动，直接中止会误伤正常评测。
        为什么也不能一直降级 NE：额度耗尽时后续**每一次**调用都会失败，
        继续跑只会产出「全维度 NE、看似正常实则无效」的报告（实测踩过：
        一次免费层额度耗尽的全量跑浪费 128 次调用并覆盖了有效基线）。
        """
        if getattr(exc, "status_code", None) != 429:
            return
        self._consecutive_rate_limits += 1
        if self._consecutive_rate_limits >= RATE_LIMIT_ABORT_AFTER:
            raise QuotaExceededError(
                f"连续 {self._consecutive_rate_limits} 次调用返回 429，判定为额度/限流耗尽，"
                f"已中止评测（继续跑只会产出全 NE 的无效报告）。"
                f"原始错误：{str(exc)[:200]}"
            ) from exc

    def judge(self, system: str, user: str, *, temperature=None, max_tokens=None) -> str:
        if self._client is None:
            return _mock_response(system, user)
        budget = max_tokens if max_tokens is not None else self.cfg.max_tokens

        def _call(tok: int):
            try:
                resp = self._client.chat.completions.create(
                    model=self.cfg.model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PREAMBLE + "\n" + system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.cfg.temperature if temperature is None else temperature,
                    max_tokens=tok,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                self._note_rate_limit(exc)  # 达阈值时抛 QuotaExceededError
                raise
            self._consecutive_rate_limits = 0  # 成功即清零
            return resp

        def _content(resp):
            """安全取首条 choice 的 content。

            实测 OpenRouter 免费层偶发返回 choices=None（200 但无候选，多为
            上游 provider 瞬时过载/限流），旧逻辑直接 resp.choices[0] 抛
            TypeError，被上游吞成误导性的「JSON 解析失败」。此处显式抛出
            可重试错误，让 base 层按「API 异常」路径重试，而非按「坏输出」。
            """
            choices = getattr(resp, "choices", None)
            if not choices:
                raise RuntimeError(
                    f"{self.cfg.model} 返回空 choices（OpenRouter 上游瞬时过载/限流，"
                    f"model={resp.model if hasattr(resp, 'model') else '?'}）。"
                    "请在稍后重试。")
            msg = getattr(choices[0], "message", None)
            return getattr(msg, "content", "") or ""

        resp = _call(budget)
        content = _content(resp)
        # 推理模型（hy3 / nemotron 等）的思维链计入 max_tokens 预算。预算吃满时
        # finish_reason=length，输出被截断——**无论 content 是否为空**。
        #
        # 踩坑记录：nemotron 在长 Judge 提示下把英文思维链直接吐进 content，
        # 8192 token 用尽时思维链写到一半、JSON 一个字未输出。此时 content
        # 非空（2.6 万字符），旧逻辑「仅当 content 为空才重试」因此不触发，
        # 截断内容被当正常结果返回 → 下游 JSON 解析失败 → 静默退化成 NE。
        # 截断即不完整，故只要 finish_reason=length 就用翻倍预算重试。
        if resp.choices[0].finish_reason == "length":
            retry_budget = min(budget * 2, 65536)
            if retry_budget > budget:
                resp = _call(retry_budget)
                content = _content(resp)
        if not content.strip():
            # 显式失败而非静默返回空串（空串会被下游解析兜底吞成假 PASS）
            finish = resp.choices[0].finish_reason if resp.choices else "?"
            raise RuntimeError(
                f"{self.cfg.model} 返回空内容（finish_reason={finish}，"
                f"max_tokens={resp.usage.completion_tokens if resp.usage else '?'}）。"
                "可能原因：① 推理模型的思维链耗尽了输出预算，请调大 HY3_MAX_TOKENS；"
                "② 该模型不支持 response_format=json_object（换用其他模型或改用提示词约束）。")
        return content

    def judge_json(self, system: str, user: str, *, temperature=None, max_tokens=None) -> Any:
        return json.loads(self.judge(system, user, temperature=temperature, max_tokens=max_tokens))


# --- 演示画像：让 mock 报告「有强弱项」，图表才有东西可画 ----------------------
# 为什么不能全 5 分：雷达图会画成满圆、维度条形图等高、双采样零分歧（一致性
# 面板永远 100%），五个图表没一个能验证到展示链路。这份画像把短板放在 AI 生成
# 课件的真实弱项上（学情分析 7、启发探究 9），教育上也说得通。
#
# 注意：短板维度（7 学情分析、9 启发探究）**不给 shift** —— 两次采样一致打低分，
# 短板才能在最终分里保留下来。若给短板加 shift，平均后会被拉到 4 分，
# 雷达图又变回接近满圆。分歧要放在中等维度上。
_MOCK_BASE = {
    "1": 5, "2": 5, "3": 4, "4": 3, "5": 5,
    "6": 5, "7": 3, "8": 4, "9": 3, "A": 5,
}
# 双采样视角差：learner_view 相对 strict_rubric 的偏移。
# 维度 4 偏移 2（3→5）会越过默认阈值 1 触发层内仲裁，一致性面板才有内容可看。
# 分数会被 clamp 到 1–5，所以想造出「分差 2」必须从 base 3 起跳。
_MOCK_LENS_SHIFT = {"3": 1, "4": 2, "8": 1}

_MOCK_DIM_SHORT = {
    "1": "教学目标", "2": "知识准确", "3": "学段适配", "4": "环节设计", "5": "表述清晰",
    "6": "安全合规", "7": "学情分析", "8": "教-学-评", "9": "启发探究", "A": "格式可读",
}


def _mock_evidence(did: str, score: int) -> str:
    name = _MOCK_DIM_SHORT.get(did, f"维度{did}")
    if score >= 5:
        return f"（演示模式）{name}：目标与证据明确，未发现实质问题。"
    if score == 4:
        return f"（演示模式）{name}：整体合格，个别环节仍有提升空间。"
    if score == 3:
        return f"（演示模式）{name}：基本达标，但存在明显短板，建议补充针对性设计。"
    return f"（演示模式）{name}：存在实质缺陷，需要重点修改后复审。"


def _mock_response(system: str, user: str) -> str:
    """演示/测试模式：返回确定性占位 JSON，不调用任何外部服务。

    演示目标：让前端能展示一份**合理且有区分度**的报告——不是模拟真实
    Judge 评分（真实评分走 `evaluate_from_text` + `HY3_*` 环境变量），
    而是把「雷达有起伏、条形有分档、双采样有分歧」的展示链路验证到位。

    维度 id 取自 system 角色对应的 JUDGE_GROUPS（fact→{2,3} /
    design→{1,4,7,8,9} / expression_safety→{5,6,A}），不再依赖正则
    抓 prompt 字面 —— 之前的正则会被 JSON schema 里的「维度id」污染，
    而且只有 fact Judge 的 prompt 真的写了「维度 X」字样。
    """
    # 1. 从 system prompt 前缀识别 Judge 角色（与 judges/*.py 的 system 字段严格对应）
    if "事实与学段裁判" in system:
        ids = ["2", "3"]
    elif "教学设计裁判" in system:
        ids = ["1", "4", "7", "8", "9"]
    elif "表达与安全裁判" in system:
        ids = ["5", "6", "A"]
    else:
        # 兜底：兼容旧 mock（按 prompt 字面抓维度 id）。排除 schema 里的「维度id」。
        raw = re.findall(r"维度\s*([0-9A-Za-z]+)", user)
        ids = [i for i in dict.fromkeys(raw) if i.lower() != "id"]

    # 2. 识别双采样视角。lens 指令由 BaseJudge._lens_directive 注入 prompt，
    #    两次采样各调一次 —— 不区分视角的话两次返回完全相同，一致性永远是 100%。
    blob = system + "\n" + user
    if "严格按上方量规" in blob:
        lens = "strict_rubric"
    elif "学习者" in blob:
        lens = "learner_view"
    else:
        lens = None

    # 3. 嗅探样本类型，对已知缺陷针对性降分（仅演示用，不影响真实评测）
    # 公式错误：02_bad_formula 末尾追加的「易错点 1/2/3」里的错误公式片段
    has_formula_bug = any(
        p in user for p in [
            "(x+3)² = x² + 9", "(x+3)²=x²+9",
            "a²+b²=c（少了一个 ²", "a²+b²=c²",
            "√(b²-4ac)", "b²-4ac 必须大于等于 0",
        ]
    )
    # 伪启发包装：03_bad_fake_socratic 末尾追加的「启发探究」节
    has_fake_socratic = ("启发探究" in user and "齐声应答" in user)

    def _score(did: str) -> int:
        # 硬伤优先于画像：有确定性缺陷的维度直接给低分
        if has_formula_bug and did == "2":
            return 1      # 知识准确性：公式错误是硬伤
        if has_fake_socratic and did == "9":
            return 1      # 启发探究（维度 9）：只有提问外壳，没有认知引导。
                          # 给 1 分而非 2 分：维度 9 权重不高，扣得轻会让伪启发样本
                          # 与好样本只差 2 分，演示时看不出系统识别出了这个问题。
        base = _MOCK_BASE.get(did, 4)
        if lens == "learner_view":
            base += _MOCK_LENS_SHIFT.get(did, 0)
        return max(1, min(5, base))

    scores = {}
    for did in ids:
        s = _score(did)
        scores[did] = {"score": s, "evidence": _mock_evidence(did, s), "ne": False}

    return json.dumps(
        {
            "admission": "PASS",
            "redline": False,
            "scores": scores,
            "suggestions": [
                "（演示模式）分数由确定性画像生成，用于验证前端展示链路，不构成真实评测结论。",
            ],
        },
        ensure_ascii=False,
    )
