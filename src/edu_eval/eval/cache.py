"""Judge 调用磁盘缓存。

命中键 = sha256(样本内容 hash + judge 角色 + 提示版本 + 温度 + 模型名)。
效果：测试—重测稳定性实验中相同输入零成本复用；不同温度/提示版本不串味。
缓存目录 results/.cache/judge/（已 gitignore）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

PROMPT_VERSION = "v1"  # 提示词改动时递增，自动失效旧缓存
CACHE_DIR = os.path.join("results", ".cache", "judge")


def cache_key(text: str, role: str, temperature: float, model: str,
              rule_evidence: str = "") -> str:
    """rule_evidence 必须参与哈希：同一段正文在不同规则判定下，
    Judge 看到的证据不同，若共用一个缓存会串味。"""
    payload = json.dumps(
        [PROMPT_VERSION, role, temperature, model, text, rule_evidence],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JudgeCache:
    def __init__(self, enabled: bool = True, cache_dir: str = CACHE_DIR):
        self.enabled = enabled
        self.cache_dir = cache_dir
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled:
            return None
        path = os.path.join(self.cache_dir, key + ".json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.hits += 1
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
        self.misses += 1
        return None

    def put(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        path = os.path.join(self.cache_dir, key + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses,
                "enabled": self.enabled}
