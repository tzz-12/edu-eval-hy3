"""验证维度 9「学习者中心与启发探究」的判别力修复是否生效。

背景
----
v4 标尺下，给好样本注入教科书级伪启发段落（"是不是这样？对不对？…无需独立思考"）
后，维度 9 只从 4 降到 3，总分 74→72 仅差 2 分 —— 判别力不足。
v5 标尺补了 2 分档 + 伪启发反模式识别 + 降级封顶 2 分。

本脚本只跑 DesignJudge（维度 1/4/7/8/9），不做 G0 准入、不做复核仲裁，
因此比完整评测省约 2/3 的调用次数，适合额度紧张时单独验证维度 9。

用法
----
    set -a; source .env; set +a
    python scripts/verify_dim9_fix.py                 # 跑 01 与 03 两份
    python scripts/verify_dim9_fix.py --threshold 2   # 自定义合格线

判定标准
--------
1. 伪启发样本（03）维度 9 必须 ≤ 2（降级规则封顶）
2. 好样本（01）维度 9 必须 ≥ 3（不得误伤真实探究）
3. 两者差值 ≥ 2（v4 时只有 1，判别力不足）

退出码 0 = 全部通过；1 = 未达预期。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.orchestrator import Orchestrator, load_kb_assets  # noqa: E402
from edu_eval.parse.parsers import parse_file  # noqa: E402

DEMO_DIR = ROOT / "data" / "samples" / "demo"

# 样本 → (文件, 期望维度9下限, 期望维度9上限)
CASES = [
    ("01_good_二次函数", "九年级", 3, 5),      # 好样本：不得被误伤
    ("03_bad_fake_socratic", "九年级", 1, 2),  # 伪启发：必须降级封顶 2
]


def run_one(orch: Orchestrator, sid: str, grade: str) -> dict:
    src = DEMO_DIR / f"{sid}.md"
    parsed = parse_file(str(src))
    context = {"grade": grade, "version": None, "topic": None, "period": None}
    # 与 Orchestrator.run 保持一致：课标素养块必须注入，否则判定口径不同
    context = {**context, "_competency": orch._competency_context()}
    rep, _ = orch._run_judge(orch.j_design, parsed.text, context)
    return (rep.get("scores") or {}).get("9") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=int, default=2,
                    help="好样本与伪启发样本的维度 9 最小分差（默认 2）")
    args = ap.parse_args()

    cfg = Hy3Config.from_env()
    if not cfg.api_key and not cfg.mock:
        print("✗ 未配置 HY3_API_KEY，请先 set -a; source .env; set +a")
        return 1

    kb, gm, retriever, warns = load_kb_assets()
    orch = Orchestrator(cfg, kb=kb, grade_map=gm, retriever=retriever,
                        warnings=warns, dual_sample=True)
    print(f"模型：{cfg.model}（mock={cfg.mock}）\n")

    results = {}
    for sid, grade, lo, hi in CASES:
        print(f"[{sid}] 跑 DesignJudge …", end=" ", flush=True)
        r = run_one(orch, sid, grade)
        score = r.get("score")
        results[sid] = score
        print(f"维度9 = {score}（期望 {lo}–{hi}）")
        print(f"     证据：{str(r.get('evidence', ''))[:160]}")
        print(f"     消解：{r.get('resolution')} a={r.get('a')} b={r.get('b')}\n")

    ok = True
    for sid, grade, lo, hi in CASES:
        s = results[sid]
        if s is None:
            print(f"✗ {sid}：维度 9 未出分（NE），无法判定")
            ok = False
        elif not (lo <= s <= hi):
            print(f"✗ {sid}：维度 9 = {s}，落在期望区间 {lo}–{hi} 之外")
            ok = False

    good = results.get("01_good_二次函数")
    bad = results.get("03_bad_fake_socratic")
    if good is not None and bad is not None:
        gap = good - bad
        print(f"判别力分差：{good} - {bad} = {gap}（要求 ≥ {args.gap}；v4 时仅 1）")
        if gap < args.gap:
            print("✗ 分差不足，维度 9 仍未拉开好样本与伪启发样本")
            ok = False

    print("\n结论：", "✓ 维度 9 判别力修复生效" if ok else "✗ 未达预期，需继续调锚点")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
