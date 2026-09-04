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


class Hy3Client:
    def __init__(self, cfg: Hy3Config):
        self.cfg = cfg
        self._client = None
        if not cfg.mock:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("未安装 openai 库，请先 `pip install openai`。") from exc
            if not cfg.base_url or not cfg.api_key:
                raise RuntimeError("HY3_BASE_URL 与 HY3_API_KEY 均不可为空。")
            self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key,
                                  timeout=cfg.timeout)

    def judge(self, system: str, user: str, *, temperature=None, max_tokens=None) -> str:
        if self._client is None:
            return _mock_response(system, user)
        budget = max_tokens if max_tokens is not None else self.cfg.max_tokens

        def _call(tok: int):
            return self._client.chat.completions.create(
                model=self.cfg.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PREAMBLE + "\n" + system},
                    {"role": "user", "content": user},
                ],
                temperature=self.cfg.temperature if temperature is None else temperature,
                max_tokens=tok,
                response_format={"type": "json_object"},
            )

        resp = _call(budget)
        choice = resp.choices[0]
        content = choice.message.content or ""
        # 推理模型（hy3 / nemotron 等）的思维链计入 max_tokens 预算。预算吃满时
        # finish_reason=length，输出被截断——**无论 content 是否为空**。
        #
        # 踩坑记录：nemotron 在长 Judge 提示下把英文思维链直接吐进 content，
        # 8192 token 用尽时思维链写到一半、JSON 一个字未输出。此时 content
        # 非空（2.6 万字符），旧逻辑「仅当 content 为空才重试」因此不触发，
        # 截断内容被当正常结果返回 → 下游 JSON 解析失败 → 静默退化成 NE。
        # 截断即不完整，故只要 finish_reason=length 就用翻倍预算重试。
        if choice.finish_reason == "length":
            retry_budget = min(budget * 2, 65536)
            if retry_budget > budget:
                resp = _call(retry_budget)
                choice = resp.choices[0]
                content = choice.message.content or ""
        if not content.strip():
            # 显式失败而非静默返回空串（空串会被下游解析兜底吞成假 PASS）
            raise RuntimeError(
                f"{self.cfg.model} 返回空内容（finish_reason={choice.finish_reason}，"
                f"max_tokens={resp.usage.completion_tokens if resp.usage else '?'}）。"
                "可能原因：① 推理模型的思维链耗尽了输出预算，请调大 HY3_MAX_TOKENS；"
                "② 该模型不支持 response_format=json_object（换用其他模型或改用提示词约束）。")
        return content

    def judge_json(self, system: str, user: str, *, temperature=None, max_tokens=None) -> Any:
        return json.loads(self.judge(system, user, temperature=temperature, max_tokens=max_tokens))


def _mock_response(system: str, user: str) -> str:
    """演示/测试模式：返回确定性占位 JSON，不调用任何外部服务。"""
    ids = re.findall(r"维度\s*([0-9A-Za-z]+)", user)
    # 排除 JSON schema 示例中的占位词“维度id”
    ids = [i for i in dict.fromkeys(ids) if i.lower() != "id"]  # 去重保序
    scores = {str(i): {"score": 3, "evidence": "（演示模式占位，未连接 Hy3）", "ne": False} for i in ids}
    return json.dumps(
        {
            "admission": "PASS",
            "redline": False,
            "scores": scores,
            "suggestions": ["（演示模式）未连接 Hy3，所有维度返回占位分 3，仅用于验证流程贯通。"],
        },
        ensure_ascii=False,
    )
