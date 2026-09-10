"""探针：验证「单维度调用」能否稳定识别轻度缺陷。

背景与假设
----------
DesignJudge 现在一次调用评 5 个维度（1/4/7/8/9），提示里要同时塞进
5 份锚点，v7 又给维度 8 和 9 各加了一份「判前必查」清单。实测出现一个
反常现象：给维度 9 补上判前必查后，维度 9 的重度缺陷从 5 分修到 2 分，
但维度 8、9 的**轻度**缺陷却同时从「能识别」退化为「给满分」。

即：**提示越长，只有重度缺陷还能被抓到，轻度信号被稀释掉了。**

本脚本验证这个假设：把「一次评 5 维」改为「一次只评 1 维」，
看轻度缺陷是否能被稳定识别。

## 结论（2026-09-10，deepseek-v4-flash，维度8 mild）——假设**证伪**

| 模式 | 各次得分 | 众数 |
|---|---|---|
| 5 维合并（系统真实行为） | 3, 3, 3 | **3** ✓ 达标且稳定 |
| 单维度 | None, None, 5 | — 反而更宽松 |

单维度调用不但没有更敏感，反而**更宽松**，且解析失败率更高。
因此**不要按维度拆分调用**，保持 DesignJudge 一次评 5 维的架构。

轻度缺陷识别不出来的真正原因不是提示长度，而是**注入不彻底**：
高质量教案的证据是分布式的，只在「评价设计」「启发探究」等单一章节
注入，文档其余环节仍留有真实证据，Judge 判高分是**正确的**。

另一处坑：单维度模式下模型偶发输出 `{"score":3,...}` 却解析失败。
原因是本脚本原先用裸 `re.search` 解析，未走 BaseJudge._parse_json +
normalize（后者会修正「维度 8」这类非 id 键）。现已改为复用系统解析，
否则会把「探针解析失败」误报成「模型没输出」。

用法
----
    # 单维度
    python scripts/probe_single_dim.py --dim 8 \
        --doc data/samples/discrimination/dim8_mild.md -n 3

    # 同模型下对照两种方式（必须用同一模型，否则无法归因）
    python scripts/probe_single_dim.py --dim 8 \
        --doc data/samples/discrimination/dim8_mild.md -n 3 --mode both

退出码 0 = 该档位分数达到预期（--expect 指定）；1 = 未达到。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval import dimensions as D  # noqa: E402
from edu_eval.eval.judges.base import BaseJudge  # noqa: E402
from edu_eval.hy3 import Hy3Client  # noqa: E402
from edu_eval.parse.parsers import parse_file  # noqa: E402

SYSTEM = ("你是教学设计裁判。依据给定量规，只对你被要求的那一个维度评分。"
          "必须引用原文片段作为证据。")


def build_prompt(text: str, dim_id: str, grade: str = "七年级上册") -> str:
    dim = D.get_dimension(dim_id)
    return (
        f"【文档元信息】年级：{grade}\n\n"
        f"【待评估教学设计正文】\n{text}\n\n"
        f"【需要评分的维度】\n### 维度 {dim.id} {dim.name}\n"
        f"{dim.description}\n锚点：\n{dim.anchors}\n\n"
        "请严格按以下 JSON 返回（score 只能是 1-5 的整数）：\n"
        '{"score": <1-5>, "evidence": "<原文片段>"}'
    )


def build_prompt_multi(text: str, dim_id: str,
                       grade: str = "七年级上册") -> str:
    """构造与 DesignJudge 完全一致的「一次评 5 维」提示，用作对照。

    只有提示构造方式与真实评测一致，对照才有意义——
    否则测的是「我手写的长提示」，不是「系统实际用的长提示」。
    """
    from edu_eval.eval.judges.design import DesignJudge

    judge = DesignJudge.__new__(DesignJudge)  # 不初始化客户端，只借 prompt 构造
    context = {"grade": grade, "version": None, "topic": None, "period": None}
    # _competency_block / _lens_directive 依赖 context，缺键时按各自默认处理；
    # 这里与 run_discrimination 的 context 保持一致，避免引入额外差异。
    return judge.system, judge.build_user_prompt(text, context)


def call_once(client: Hy3Client, prompt: str, dim: str,
              system: str = SYSTEM) -> tuple[int | None, str]:
    raw = client.judge(system, prompt)

    # 必须复用 BaseJudge 的解析与归一化：
    # 实测 deepseek-v4-flash 偶发把 scores 键写成「维度 8」等非 id 形式，
    # 裸解析会得到 None，但系统 normalize 能修正回来。探针若不做同样处理，
    # 就会把「探针解析失败」误报成「模型没输出」。
    data = BaseJudge._parse_json(raw)
    if data.get("_parse_failed"):
        return None, str(data.get("_raw_excerpt") or "")[:200]
    data = BaseJudge.normalize(data)

    # 兼容两种返回：单维度格式 {"score":..} 与批量格式 {"scores":{"8":{"score":..}}}
    # （mock 与部分模型会按批量格式返回，不兼容就永远解析不出分）
    if "score" in data:
        sc = data.get("score")
        ev = str(data.get("evidence") or "")
    else:
        entry = (data.get("scores") or {}).get(dim) or {}
        sc = entry.get("score")
        ev = str(entry.get("evidence") or "")
    if isinstance(sc, list):
        sc = sc[0] if sc else None
    try:
        sc = int(sc)
    except (TypeError, ValueError):
        return None, str(data)[:200]
    return sc, ev[:300]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dim", required=True, help="要评的维度 id，如 8")
    ap.add_argument("--doc", required=True)
    ap.add_argument("-n", type=int, default=3, help="重复次数")
    ap.add_argument("--expect", type=int, default=None,
                    help="预期分数（众数应等于它）")
    ap.add_argument("--mode", choices=["single", "multi", "both"],
                    default="single",
                    help="single=一次只评该维度；multi=按 DesignJudge 原样评 5 维；"
                         "both=两种都跑并对照（同一模型下才能归因）")
    args = ap.parse_args()

    cfg = Hy3Config.from_env()
    if not cfg.api_key and not cfg.mock:
        print("✗ 未配置 HY3_API_KEY")
        return 1

    text = parse_file(args.doc).text
    print(f"文档：{Path(args.doc).name}（{len(text)} 字）\n")
    client = Hy3Client(cfg)

    modes = [args.mode] if args.mode != "both" else ["single", "multi"]
    results: dict[str, list] = {}

    for mode in modes:
        if mode == "single":
            sysmsg, prompt = SYSTEM, build_prompt(text, args.dim)
        else:
            sysmsg, prompt = build_prompt_multi(text, args.dim)
        label = "单维度" if mode == "single" else "5维合并"
        print(f"── {label}（提示 {len(prompt)} 字符）──")

        scores = []
        for i in range(1, args.n + 1):
            sc, ev = call_once(client, prompt, args.dim, sysmsg)
            scores.append(sc)
            print(f"  第 {i}/{args.n} 次 → 维度{args.dim} = {sc}")
            if i == 1:
                print(f"    证据：{ev}")
        results[mode] = scores
        valid = [s for s in scores if s is not None]
        if valid:
            md = max(set(valid), key=valid.count)
            print(f"  各次得分：{scores}　众数：{md}\n")
        else:
            print("  ✗ 无有效得分\n")

    if args.mode == "both":
        s = [x for x in results.get("single", []) if x is not None]
        m = [x for x in results.get("multi", []) if x is not None]
        if s and m:
            print("═" * 46)
            print(f"对照结论：单维度均值 {sum(s)/len(s):.2f}　"
                  f"5维合并均值 {sum(m)/len(m):.2f}")
            if sum(s) / len(s) < sum(m) / len(m):
                print("→ 单维度调用对轻度缺陷更敏感（合并调用稀释了信号）")
            elif sum(s) / len(s) > sum(m) / len(m):
                print("→ 单维度调用反而更宽松，假设不成立")
            else:
                print("→ 两者无差异，提示长度不是主因")

    all_valid = [x for v in results.values() for x in v if x is not None]
    if not all_valid:
        print("✗ 无有效得分")
        return 1

    if args.expect is not None:
        # 以最后一种模式（both 时为 multi，即系统真实行为）的众数判定
        last = [x for x in results.get(modes[-1], []) if x is not None]
        md = max(set(last), key=last.count) if last else None
        ok = md == args.expect
        print(f"预期 {args.expect}（{modes[-1]} 众数 {md}）→ "
              f"{'✓ 符合' if ok else '✗ 不符'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
