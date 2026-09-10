"""稳定性（测试—重测）实验（DESIGN.md §5.2「稳定性与跨格式一致性」）。

方案承诺：冻结配置下测试—重测平均绝对分差 ≤ 0.5 分。

为什么必须先测这个
------------------
判别力实验依赖「同一底稿的基准分」作为比较锚点。若基准分自己就漂 ±2 分，
那么"缺陷导致下降 1 分"完全可能只是噪声 —— 判别力结论就不可信。
实测已观察到底稿跨运行漂移（D9 一次 5 分一次 3 分），故先量化稳定性。

实现要点
--------
**必须绕过 Judge 缓存**：相同 prompt + 相同 PROMPT_VERSION 会命中缓存直接返回，
那测到的是"缓存一致性"（必然 100%），而不是真实的模型输出波动。
本脚本每次迭代都换一个全新的缓存目录。

用法
----
    set -a; source .env; set +a
    python scripts/run_stability.py                       # 默认 3 次
    python scripts/run_stability.py -n 5                  # 跑 5 次
    python scripts/run_stability.py --doc <path>          # 指定文档

退出码 0 = 平均绝对分差 ≤ 阈值（默认 0.5）；1 = 超出。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.orchestrator import Orchestrator, load_kb_assets  # noqa: E402
from edu_eval.parse.parsers import parse_file  # noqa: E402

DEFAULT_DOC = ROOT / "data" / "samples" / "discrimination" / "base.md"
DIMS = ["1", "4", "7", "8", "9"]


def run_once(cfg: Hy3Config, text: str, grade: str) -> dict:
    kb, gm, ret, w = load_kb_assets()
    orch = Orchestrator(cfg, kb=kb, grade_map=gm, retriever=ret,
                        warnings=w, dual_sample=False)
    ctx = {"grade": grade, "version": None, "topic": None, "period": None}
    ctx = {**ctx, "_competency": orch._competency_context()}
    rep, _ = orch._run_judge(orch.j_design, text, ctx)
    return {d: (rep.get("scores", {}).get(d) or {}).get("score") for d in DIMS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=3, help="重复次数（默认 3）")
    ap.add_argument("--doc", default=str(DEFAULT_DOC))
    ap.add_argument("--grade", default="七年级上册")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="平均绝对分差阈值（方案要求 ≤0.5）")
    ap.add_argument("--out", default="results/stability.json",
                    help="结果输出路径（测多份文档时另存，避免互相覆盖）")
    args = ap.parse_args()

    doc = Path(args.doc)
    if not doc.is_absolute():
        doc = ROOT / doc          # 相对路径按项目根解析，否则 relative_to 会炸
    if not doc.exists():
        print(f"✗ 文档不存在：{doc}")
        return 1

    parsed = parse_file(str(doc))
    print(f"文档：{doc.name}（{len(parsed.text)} 字）")
    print(f"重复次数：{args.n}   温度：0.0（冻结配置）\n")

    runs = []
    for i in range(1, args.n + 1):
        # 关键：每次换全新缓存目录，否则命中缓存测不出真实波动
        os.environ["EDU_EVAL_CACHE_DIR"] = tempfile.mkdtemp(
            prefix=f"stab{i}_")
        cfg = Hy3Config.from_env()
        print(f"第 {i}/{args.n} 次 …", end=" ", flush=True)
        s = run_once(cfg, parsed.text, args.grade)
        runs.append(s)
        print(" ".join(f"D{d}={'-' if s[d] is None else s[d]}" for d in DIMS))

    print("\n" + "=" * 60)
    print(f"{'维度':<6}{'各次得分':<18}{'极差':<8}{'平均绝对偏差'}")
    print("-" * 60)
    devs = []
    for d in DIMS:
        vals = [r[d] for r in runs]
        if any(v is None for v in vals):
            print(f"D{d:<5}{str(vals):<18}—       有维度未出分（NE），不计入")
            continue
        rng = max(vals) - min(vals)
        mad = statistics.mean(abs(v - statistics.mean(vals)) for v in vals)
        devs.append(mad)
        print(f"D{d:<5}{str(vals):<18}{rng:<8}{mad:.2f}")
    print("-" * 60)

    if not devs:
        print("✗ 无有效数据")
        return 1

    overall = statistics.mean(devs)
    worst = max(devs)
    print(f"平均绝对偏差（全体维度）：{overall:.2f}   最大：{worst:.2f}   "
          f"阈值：{args.threshold}")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "doc": str(doc.relative_to(ROOT)), "runs": args.n, "runs_detail": runs,
        "mean_abs_dev": overall, "max_abs_dev": worst, "threshold": args.threshold,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写入：{out.relative_to(ROOT)}")

    ok = overall <= args.threshold
    print("\n结论：", "✓ 稳定性达标" if ok
          else f"✗ 稳定性不达标（{overall:.2f} > {args.threshold}），"
               "判别力结论需谨慎解读")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
