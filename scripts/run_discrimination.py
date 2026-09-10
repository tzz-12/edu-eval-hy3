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
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.cache import JudgeCache  # noqa: E402
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


def score_repeat(orch: Orchestrator, path: Path, dim: str, n: int):
    """同一文档重复采样 n 次，取**众数**作为该样本的分数。

    为什么要重复采样
    ----------------
    实测发现：干净底稿（明确高质量）5 次重测完全一致，MAD=0.00；
    但缺陷样本会落在决策边界上摇摆——dim9_severe 三次得分为 [2, 5, 2]，
    单次采样抽到 5 就会误判成"完全没识别"，抽到 2 才是它的稳定行为。
    判别力结论若建立在单次采样上，本质是掷骰子。

    返回（众数分数, 各次原始分数, 众数对应的证据）。
    """
    scores, evidences = [], []
    for _ in range(n):
        s = score_one(orch, path)
        scores.append((s.get(dim) or {}).get("score"))
        evidences.append((s.get(dim) or {}).get("evidence") or "")
    valid = [x for x in scores if x is not None]
    if not valid:
        return None, scores, ""
    try:
        mode = statistics.mode(valid)
    except statistics.StatisticsError:
        mode = valid[0]
    idx = next(i for i, x in enumerate(scores) if x == mode)
    return mode, scores, evidences[idx]


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
    ap.add_argument("--repeat", type=int, default=1,
                    help="每份样本重复采样次数，取众数（默认 1；缺陷样本建议 3）")
    args = ap.parse_args()

    dims = [d.strip() for d in args.dims.split(",") if d.strip()]
    cfg = Hy3Config.from_env()
    if not cfg.api_key and not cfg.mock:
        print("✗ 未配置 HY3_API_KEY，请先 set -a; source .env; set +a")
        return 1

    kb, gm, retriever, warns = load_kb_assets()
    orch = Orchestrator(cfg, kb=kb, grade_map=gm, retriever=retriever,
                        warnings=warns, dual_sample=False)
    # 重复采样必须禁用缓存：同 prompt + 同文本会命中缓存直接返回，
    # 那样"采样 3 次"实际是同一个结果，测不出真实的边界摇摆。
    if args.repeat > 1:
        orch.j_design.cache = JudgeCache(enabled=False)
    print(f"模型：{cfg.model}（mock={cfg.mock}，双采样=关，"
          f"重复采样={args.repeat}）\n")

    man = load_manifest()
    cases = [c for c in man["cases"] if c["dim"] in dims]

    # base 各维度的众数（每个维度各自聚合，因为一次调用出全部维度）
    print("[base] 跑干净底稿 …", end=" ", flush=True)
    # 一次调用会同时返回全部维度，所以只跑 repeat 次再按维度聚合，
    # 而不是每个维度各跑 repeat 次（否则白白多花 N 倍调用）。
    base_runs = [score_one(orch, SAMPLE_DIR / "base.md")
                 for _ in range(args.repeat)]
    base_scores: dict[str, object] = {}
    base_raw: dict[str, list] = {}
    base_ev: dict[str, str] = {}
    for d in dims:
        vals = [(s.get(d) or {}).get("score") for s in base_runs]
        evs = [(s.get(d) or {}).get("evidence") or "" for s in base_runs]
        base_raw[d] = vals
        valid = [v for v in vals if v is not None]
        if not valid:
            base_scores[d], base_ev[d] = None, ""
            continue
        try:
            mode = statistics.mode(valid)
        except statistics.StatisticsError:
            mode = valid[0]
        base_scores[d] = mode
        base_ev[d] = evs[next(i for i, v in enumerate(vals) if v == mode)]
    print("完成 →", " ".join(
        f"D{d}={fmt(base_scores[d])}" +
        (f"{base_raw[d]}" if args.repeat > 1 else "") for d in dims))

    results: dict[str, dict[str, dict]] = {d: {} for d in dims}
    for c in cases:
        print(f"[{c['case_id']}] …", end=" ", flush=True)
        sc, raw, ev = score_repeat(orch, SAMPLE_DIR / f"{c['case_id']}.md",
                                   c["dim"], args.repeat)
        results[c["dim"]][c["level"]] = {"score": sc, "evidence": ev,
                                         "raw": raw,
                                         "case": c["case_id"]}
        hit = any(k in ev for k in LOCATE_HINT.get(c["dim"], []))
        print(f"维度{c['dim']}={fmt(sc)}"
              + (f" 各次{raw}" if args.repeat > 1 else "")
              + f"  证据定位={'✓' if hit else '✗'}")

    # ---- 汇总 ----
    print("\n" + "=" * 66)
    print(f"{'维度':<6}{'base':<8}{'mild':<8}{'severe':<8}{'判定'}")
    print("-" * 66)
    all_ok = True
    for d in dims:
        b = base_scores.get(d)
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
                 if base_scores.get(d) is not None
                 and results[d].get("mild", {}).get("score") is not None
                 and results[d].get("severe", {}).get("score") is not None
                 and (base_scores[d] - results[d]["severe"]["score"]) >= args.min_drop
                 and (base_scores[d] - results[d]["mild"]["score"]) >= args.min_drop
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
        "repeat": args.repeat,
        "base": {d: base_scores.get(d) for d in dims},
        "base_raw": {d: base_raw.get(d) for d in dims},
        "cases": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        shown = out.relative_to(ROOT)
    except ValueError:          # --out 给了项目外的绝对路径
        shown = out
    print(f"\n明细已写入：{shown}")

    print("\n结论：", "✓ 判别力达标" if all_ok else "✗ 判别力不足，需修 rubric")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
