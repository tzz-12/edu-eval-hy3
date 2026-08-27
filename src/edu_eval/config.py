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
    max_tokens: int = 2048
    timeout: float = 90.0
    # mock 模式：无密钥时返回确定性占位结果，便于本地试用与自动化测试
    mock: bool = False

    @classmethod
    def from_env(cls, require_key: bool = True) -> "Hy3Config":
        base_url = (os.getenv("HY3_BASE_URL") or "").strip()
        api_key = (os.getenv("HY3_API_KEY") or "").strip()
        model = (os.getenv("HY3_MODEL") or "hunyuan-turbo").strip()
        mock = (os.getenv("HY3_MOCK") or "0").strip() in ("1", "true", "True")

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

        return cls(base_url=base_url, api_key=api_key, model=model, mock=mock)
