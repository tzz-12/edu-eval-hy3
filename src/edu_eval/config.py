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
    timeout: float = 90.0
    # mock 模式：无密钥时返回确定性占位结果，便于本地试用与自动化测试
    mock: bool = False

    @classmethod
    def from_env(cls, require_key: bool = True) -> "Hy3Config":
        base_url = (os.getenv("HY3_BASE_URL") or "").strip()
        api_key = (os.getenv("HY3_API_KEY") or "").strip()
        model = (os.getenv("HY3_MODEL") or "hunyuan-turbo").strip()
        mock = (os.getenv("HY3_MOCK") or "0").strip() in ("1", "true", "True")
        max_tokens_raw = (os.getenv("HY3_MAX_TOKENS") or "").strip()
        max_tokens = int(max_tokens_raw) if max_tokens_raw.isdigit() else 8192

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
                   max_tokens=max_tokens, mock=mock)
