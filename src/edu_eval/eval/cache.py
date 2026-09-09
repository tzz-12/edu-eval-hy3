"""Judge 调用磁盘缓存。

命中键 = sha256(提示版本 + judge 角色 + 温度 + 模型 + **完整渲染后的 system/user 提示**
                + 规范化后的 context)。

效果：测试—重测稳定性实验中相同输入零成本复用；不同温度/提示版本不串味。
缓存目录 results/.cache/judge/（已 gitignore）。

P0-10 · E 修复
--------------
早期版本只对 `text + role + temperature + model + rule_evidence` 取哈希，漏掉了
两项会影响 Judge 判断的输入：

* `kb_context` —— 注入提示的知识库条目；
* `context`    —— 用户声明的年级 / 版本 / 课题 / 课时。

后果（实测）：同一份文本先按七年级跑、再按九年级跑，第二次**全部命中第一次的
缓存**，于是「声明年级与实际内容不一致 → 质量风险信号」这一核心设计直接失效，
且实验数据无法复现。

修法不是把漏掉的字段逐个补进参数列表（下次再加输入还会漏），而是改为
**对最终渲染出的完整提示取哈希**：任何进入提示的内容自动进入缓存键。
`context` 另外显式参与一次，用于兜住「传入但未渲染进提示」的元数据。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

PROMPT_VERSION = "v6"  # v6: 维度4/8 锚点新增「判前必查」清单与降级规则（旧缓存自动失效）
# 版本沿革：v3 维度锚点嵌入核心素养定语 + 新增 competency_link 字段


def _stable(obj: Any) -> Any:
    """把任意结构规整为可稳定序列化的形式（字典按键排序）。"""
    if isinstance(obj, dict):
        return {str(k): _stable(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_stable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def cache_key(role: str, temperature: float, model: str,
              system_prompt: str, user_prompt: str,
              context: Optional[Any] = None) -> str:
    """对**完整渲染后的提示**取哈希，杜绝「新输入忘了进键」的串味。"""
    payload = json.dumps(
        [PROMPT_VERSION, role, temperature, model,
         system_prompt, user_prompt, _stable(context)],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JudgeCache:
    def __init__(self, enabled: bool = True, cache_dir: Optional[str] = None):
        from .. import paths as P  # 延迟导入：避免与 paths 形成循环依赖

        self.enabled = enabled
        self.cache_dir = cache_dir or P.cache_dir()
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
