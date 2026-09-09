"""教学缺陷判别力实验（DESIGN.md §4.4 · 验收指标「教学缺陷判别力」）。

实验设计
--------
在**同一份干净底稿**上注入非知识类教学缺陷，分三档：
    base（无缺陷） → mild（形似神不似） → severe（直接缺失）
只跑 DesignJudge（维度 1/4/7/8/9），关闭双采样（本实验测判别力，非一致性，
关闭可省一半调用）。

判定标准（每条对应方案里的一句承诺）
--------------------------------
1. 重度有效性  base − severe ≥ 1 ：重度缺陷必须被抓到，不能被其他环节的优秀抵消
2. 轻度敏感性  base − mild  ≥ 1 ：轻度包装必须被识破（这是判别力的真正考验）
3. 单调性      mild ≥ severe     ：缺陷越重分越低，不得倒挂

辅助观察：证据是否引用到注入片段（对应「报告证据能够定位到注入位置」）。

用法
----
    set -a; source .env; set +a
    python scripts/run_discrimination.py                 # 全量 11 份
    python scripts/run_discrimination.py --dims 9        # 只跑维度 9

退出码 0 = 三条判定全部通过；1 = 存在未通过项。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.orchestrator import Orchestrator, load_kb_assets  # noqa: E402
from edu_eval.parse.parsers import parse_file  # noqa: E402

SAMPLE_DIR = ROOT / "data" / "samples" / "discrimination"
GRADE = "七年级上册"

# 注入片段的关键词，用于检查证据是否定位到注入位置
LOCATE_HINT = {
    "1": ["理解", "掌握", "素养", "全面发展"],
    "4": ["教师讲解", "教师板书", "教师演示", "自行把握"],
    "7": ["参差不齐", "因材施教", "基本情况一般"],
    "8": ["表现打分", "登记分数", "考试分数"],
    "9": ["齐声", "是不是", "对不对", "无需学生独立思考", "记忆并默写",
          "不设提问", "观看演示", "抄写性质条文"],
}


def load_manifest() -> dict:
    return json.loads((SAMPLE_DIR / "manifest.json").read_text(encoding="utf-8"))


def score_one(orch: Orchestrator, path: Path) -> dict:
    parsed = parse_file(str(path))
    context = {"grade": GRADE, "version": None, "topic": None, "period": None}
    context = {**context, "_competency": orch._competency_context()}
    rep, _ = orch._run_judge(orch.j_design, parsed.text, context)
    return rep.get("scores") or {}


def fmt(v):
    return "—" if v is None else str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dims", default="1,4,7,8,9",
                    help="要跑的维度，逗号分隔（默认 1,4,7,8,9）")
    ap.add_argument("--min-drop", type=int, default=1,
                    help="base 与缺陷档的最小分差（默认 1）")
    ap.add_argument("--out", default="results/discrimination.json",
                    help="结果输出路径（只跑部分维度时另存，避免覆盖全量结果）")
    args = ap.parse_args()

    dims = [d.strip() for d in args.dims.split(",") if d.strip()]
    cfg = Hy3Config.from_env()
    if not cfg.api_key and not cfg.mock:
        print("✗ 未配置 HY3_API_KEY，请先 set -a; source .env; set +a")
        return 1

    kb, gm, retriever, warns = load_kb_assets()
    orch = Orchestrator(cfg, kb=kb, grade_map=gm, retriever=retriever,
                        warnings=warns, dual_sample=False)
    print(f"模型：{cfg.model}（mock={cfg.mock}，双采样=关）\n")

    man = load_manifest()
    cases = [c for c in man["cases"] if c["dim"] in dims]

    # base 只需跑一次
    print("[base] 跑干净底稿 …", end=" ", flush=True)
    base_scores = score_one(orch, SAMPLE_DIR / "base.md")
    print("完成 →", " ".join(f"D{d}={fmt((base_scores.get(d) or {}).get('score'))}"
                            for d in dims))

    results: dict[str, dict[str, dict]] = {d: {} for d in dims}
    for c in cases:
        print(f"[{c['case_id']}] …", end=" ", flush=True)
        s = score_one(orch, SAMPLE_DIR / f"{c['case_id']}.md")
        sc = (s.get(c["dim"]) or {}).get("score")
        ev = (s.get(c["dim"]) or {}).get("evidence") or ""
        results[c["dim"]][c["level"]] = {"score": sc, "evidence": ev,
                                         "case": c["case_id"]}
        hit = any(k in ev for k in LOCATE_HINT.get(c["dim"], []))
        print(f"维度{c['dim']}={fmt(sc)}  证据定位={'✓' if hit else '✗'}")

    # ---- 汇总 ----
    print("\n" + "=" * 66)
    print(f"{'维度':<6}{'base':<8}{'mild':<8}{'severe':<8}{'判定'}")
    print("-" * 66)
    all_ok = True
    for d in dims:
        b = (base_scores.get(d) or {}).get("score")
        m = results[d].get("mild", {}).get("score")
        s = results[d].get("severe", {}).get("score")
        if b is None or m is None or s is None:
            print(f"D{d:<5}{fmt(b):<8}{fmt(m):<8}{fmt(s):<8}✗ 有维度未出分（NE）")
            all_ok = False
            continue
        drop_s, drop_m = b - s, b - m
        checks = []
        if drop_s < args.min_drop:
            checks.append(f"重度未识别(Δ{drop_s})")
        if drop_m < args.min_drop:
            checks.append(f"轻度未识别(Δ{drop_m})")
        if m < s:
            checks.append("倒挂(mild<severe)")
        ok = not checks
        all_ok &= ok
        print(f"D{d:<5}{b:<8}{m:<8}{s:<8}{'✓' if ok else '✗ ' + '；'.join(checks)}")

    print("-" * 66)
    n = len(dims)
    passed = sum(1 for d in dims
                 if (base_scores.get(d) or {}).get("score") is not None
                 and results[d].get("mild", {}).get("score") is not None
                 and results[d].get("severe", {}).get("score") is not None
                 and (base_scores[d]["score"] - results[d]["severe"]["score"]) >= args.min_drop
                 and (base_scores[d]["score"] - results[d]["mild"]["score"]) >= args.min_drop
                 and results[d]["mild"]["score"] >= results[d]["severe"]["score"])
    print(f"顺序正确率（设计目标 ≥90%）：{passed}/{n} = "
          f"{passed / n * 100:.0f}%" if n else "")

    # 证据定位
    located = sum(1 for d in dims for lv in ("mild", "severe")
                  if any(k in (results[d].get(lv, {}).get("evidence") or "")
                         for k in LOCATE_HINT.get(d, [])))
    total = len(dims) * 2
    print(f"证据定位率（设计目标 ≥80%）：{located}/{total} = "
          f"{located / total * 100:.0f}%" if total else "")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": cfg.model,
        "base": {d: (base_scores.get(d) or {}).get("score") for d in dims},
        "cases": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已写入：{out.relative_to(ROOT)}")

    print("\n结论：", "✓ 判别力达标" if all_ok else "✗ 判别力不足，需修 rubric")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
