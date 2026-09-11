"""配置加载：所有密钥仅来自环境变量，绝不硬编码进代码或提交仓库。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Hy3Config:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    # hy3 为推理模型：思维链（reasoning_tokens）计入输出预算，
    # 2048 会被思维链吃满导致 content 为空（finish_reason=length），
    # 故默认提高至 8192，可用环境变量 HY3_MAX_TOKENS 覆盖。
    max_tokens: int = 8192
    # 单次 API 调用超时（秒）。此前该值从未传给 openai 客户端，实际用的是
    # 库默认 600s，导致「配了 90s 却等 10 分钟」。现真正生效，可用
    # HY3_TIMEOUT 覆盖。默认 300s：推理模型长 Judge 实测 40–200s。
    timeout: float = 300.0
    # mock 模式：无密钥时返回确定性占位结果，便于本地试用与自动化测试
    mock: bool = False

    @classmethod
    def from_env(cls, require_key: bool = True) -> "Hy3Config":
        base_url = (os.getenv("HY3_BASE_URL") or "").strip()
        api_key = (os.getenv("HY3_API_KEY") or "").strip()
        model = (os.getenv("HY3_MODEL") or "hy3").strip()
        mock = (os.getenv("HY3_MOCK") or "0").strip() in ("1", "true", "True")
        max_tokens_raw = (os.getenv("HY3_MAX_TOKENS") or "").strip()
        max_tokens = int(max_tokens_raw) if max_tokens_raw.isdigit() else 8192
        timeout_raw = (os.getenv("HY3_TIMEOUT") or "").strip()
        try:
            timeout = float(timeout_raw)
        except ValueError:
            timeout = 300.0

        if not base_url:
            if not mock:
                raise RuntimeError(
                    "缺少环境变量 HY3_BASE_URL。请复制 .env.example 为 .env 并填入 Hy3 端点，"
                    "或使用 HY3_MOCK=1 启用演示模式。"
                )
            base_url = "https://example.invalid/v1"  # mock 下不会被真正请求

        if not api_key:
            if require_key and not mock:
                raise RuntimeError(
                    "缺少环境变量 HY3_API_KEY。请复制 .env.example 为 .env 并填入密钥，"
                    "或使用 HY3_MOCK=1 启用演示模式。切勿将密钥硬编码进代码或提交仓库。"
                )
            mock = True

        return cls(base_url=base_url, api_key=api_key, model=model,
                   max_tokens=max_tokens, timeout=timeout, mock=mock)
