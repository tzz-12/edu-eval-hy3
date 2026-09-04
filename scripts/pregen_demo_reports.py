"""预生成 3 个演示样本的评测报告，用于 demo 快捷入口的秒级返回。

每个样本对应 data/samples/demo/ 下同名前缀的 markdown；
跑出来的 Report JSON 落到 data/demo_reports/<id>.json。

运行前置：HY3_API_KEY / HY3_BASE_URL / HY3_MODEL 已配置。
若额度耗尽或 key 未配，逐一报错并跳过（不阻断）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _json_default(o):
    """sympy Integer/Float/Bool 等非原生类型 → 原生 Python 值。"""
    if hasattr(o, "is_integer") and o.is_integer:
        return int(o)
    if hasattr(o, "is_number") and hasattr(o, "as_real_imag"):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return list(o)
    return str(o)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.run_eval import EvalContext, evaluate_from_text  # noqa: E402

DEMO_DIR = ROOT / "data" / "samples/demo"
OUT_DIR = ROOT / "data" / "demo_reports"


# 演示样本 id → (markdown 路径, 年级, 期望 admission)
SAMPLES = [
    ("01_good_二次函数", "九年级"),
    ("02_bad_formula", "九年级"),
    ("03_bad_fake_socratic", "九年级"),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    key = os.environ.get("HY3_API_KEY", "").strip()
    if not key:
        print("✗ 未配置 HY3_API_KEY，跳过预生成")
        print("  请先 set -a; source .env; set +a 再跑本脚本")
        return 1

    cfg = Hy3Config.from_env()
    print(f"使用模型：{cfg.model} @ {cfg.base_url}\n")

    ok = 0
    for sid, grade in SAMPLES:
        src = DEMO_DIR / f"{sid}.md"
        if not src.exists():
            print(f"✗ 样本不存在：{src}")
            continue
        out = OUT_DIR / f"{sid}.json"
        print(f"[{sid}] 跑 {src.name} …", end=" ", flush=True)
        t0 = time.time()
        try:
            report = evaluate_from_text(
                src.read_text(encoding="utf-8"),
                cfg,
                EvalContext(grade=grade),
                dual_sample=True,
                dual_threshold=1,
            )
            payload = report.to_dict()
            payload["_demo_source"] = src.name
            payload["_demo_grade"] = grade
            out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
                encoding="utf-8",
            )
            dt = time.time() - t0
            adm = payload["admission"]
            score = (payload.get("aggregation") or {}).get("total_score", "—")
            ds = payload.get("dual_sample") or {}
            ds_info = (
                f"双采样 {ds.get('avg_path','-')}/{ds.get('arbitrated','-')}"
                if ds.get("dims_total") else "单采样"
            )
            print(f"✓ {dt:.0f}s · admission={admission_color(adm)} 总分={score} ({ds_info})")
            ok += 1
        except Exception as e:
            print(f"✗ {type(e).__name__}: {str(e)[:120]}")
    print(f"\n完成 {ok}/{len(SAMPLES)} 份；输出：{OUT_DIR}")
    return 0 if ok == len(SAMPLES) else 2


def admission_color(adm: str) -> str:
    return {"PASS": "\033[32mPASS\033[0m",
            "FAIL": "\033[31mFAIL\033[0m",
            "NE":   "\033[90mNE\033[0m"}.get(adm, adm)


if __name__ == "__main__":
    raise SystemExit(main())
