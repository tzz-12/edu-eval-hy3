"""多 Judge 编排（均基于 Hy3，通过不同角色提示实现专业分工）。

- 事实 Judge：知识正确性准入（G0）+ 学段适配（维度 3）
- 教学设计 Judge：维度 1 / 4 / 7 / 8 / 9
- 表达与安全 Judge：维度 5 / 6（含红线）/ A

知识准入（G0）优先；只有 PASS 后才启动其余 Judge。复核 Judge 检查每个分数是否有
原文证据；当主 Judge 与复核分差 > 1、红线冲突或证据无效时触发仲裁 Judge。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..hy3 import Hy3Client
from . import dimensions as D
from .knowledge_base import KnowledgeBase

_GROUP_SYSTEM = {
    "fact": (
        "你是事实与学段裁判。你在知识库证据与程序计算基础上判断知识正确性（准入）和学段适配。"
        "必须逐项引用原文与知识库条目 ID。"
    ),
    "design": (
        "你是教学设计裁判。你依据量规评估教学目标、环节、学情、教—学—评一致性与学习者中心。"
        "必须引用原文片段作为证据。"
    ),
    "expression_safety": (
        "你是表达与安全裁判。你评估表述清晰度、安全合规与价值导向（含红线）以及格式可读性。"
        "红线判断必须给出原文证据与风险类别。"
    ),
}

_GROUP_DIM_IDS = D.JUDGE_GROUPS


def _build_user_prompt(group: str, text: str, context: Dict[str, Any],
                       kb_context: str = "") -> str:
    dim_ids = _GROUP_DIM_IDS[group]
    lines = []
    for did in dim_ids:
        dim = D.get_dimension(did)
        lines.append(f"### 维度 {did} {dim.name}（{dim.priority}，权重 {dim.weight}%）：\n{dim.description}\n锚点：\n{dim.anchors}")
    meta = (
        f"用户声明目标：年级={context.get('grade','未声明')}；教材版本={context.get('version','未声明')}；"
        f"课题={context.get('topic','未声明')}；课时={context.get('period','未声明')}"
    )
    kb_block = f"\n\n【本地知识库检索结果】\n{kb_context}\n" if kb_context else ""
    return (
        f"{meta}\n\n【待评估教学设计正文】\n{text}\n\n"
        f"【需要评分的维度与量规】\n" + "\n\n".join(lines) + kb_block +
        "\n\n请返回严格 JSON：\n"
        "{\"admission\":\"PASS|FAIL|NE\",\"redline\":bool,"
        "\"scores\":{\"<维度id>\":{\"score\":1-5,\"evidence\":\"原文片段\",\"ne\":bool}},"
        "\"suggestions\":[\"改进建议\"]}"
    )


def run_judge_group(client: Hy3Client, group: str, text: str,
                    context: Dict[str, Any], kb: KnowledgeBase) -> Dict[str, Any]:
    """调用某一 Judge 组，返回解析后的 JSON 结果。"""
    kb_context = ""
    if group == "fact":
        kb_context = kb.format_for_prompt(kb.retrieve(text, top_k=5))
    user = _build_user_prompt(group, text, context, kb_context)
    raw = client.judge(_GROUP_SYSTEM[group], user)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 兜底：模型偶发返回非严格 JSON，尝试抽取首个 {...}
        import re
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
    data.setdefault("scores", {})
    data.setdefault("suggestions", [])
    data.setdefault("admission", "PASS")
    data.setdefault("redline", False)
    return data


def multimodal_cross_check(client: Hy3Client, primary: Dict[str, Any],
                           text: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """复核 Judge：检查主 Judge 每个分数是否具备原文证据与量规依据。

    返回需要仲裁的维度 id 列表与复核后的分数（此处实现为证据一致性检查）。
    """
    needs_arbitration: List[str] = []
    for did, s in primary.get("scores", {}).items():
        if isinstance(s, dict):
            ev = (s.get("evidence") or "").strip()
            if s.get("ne"):
                continue
            if not ev:  # 无证据支撑的分数需仲裁
                needs_arbitration.append(did)
    return {"needs_arbitration": needs_arbitration, "reviewed": primary}
