"""探针：验证「单维度调用」能否稳定识别轻度缺陷。

背景与假设
----------
DesignJudge 现在一次调用评 5 个维度（1/4/7/8/9），提示里要同时塞进
5 份锚点，v7 又给维度 8 和 9 各加了一份「判前必查」清单。实测出现一个
反常现象：给维度 9 补上判前必查后，维度 9 的重度缺陷从 5 分修到 2 分，
但维度 8、9 的**轻度**缺陷却同时从「能识别」退化为「给满分」。

即：**提示越长，只有重度缺陷还能被抓到，轻度信号被稀释掉了。**

本脚本验证这个假设：把「一次评 5 维」改为「一次只评 1 维」，
提示长度约降为 1/5，看轻度缺陷是否能被稳定识别。

若验证通过，说明真正的修复方向是**按维度拆分调用**，而不是继续往
提示里加规则——加规则会让它更长，反而更糟。

用法
----
    python scripts/probe_single_dim.py --dim 8 \
        --doc data/samples/discrimination/dim8_mild.md -n 3

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


def call_once(client: Hy3Client, prompt: str, dim: str) -> tuple[int | None, str]:
    raw = client.judge(SYSTEM, prompt)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None, raw[:200]
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, raw[:200]

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
    args = ap.parse_args()

    cfg = Hy3Config.from_env()
    if not cfg.api_key and not cfg.mock:
        print("✗ 未配置 HY3_API_KEY")
        return 1

    text = parse_file(args.doc).text
    prompt = build_prompt(text, args.dim)
    print(f"文档：{Path(args.doc).name}（{len(text)} 字）")
    print(f"提示长度：{len(prompt)} 字符（单维度）\n")

    client = Hy3Client(cfg)
    scores = []
    for i in range(1, args.n + 1):
        sc, ev = call_once(client, prompt, args.dim)
        scores.append(sc)
        print(f"第 {i}/{args.n} 次 → 维度{args.dim} = {sc}")
        if i == 1:
            print(f"  证据：{ev}\n")

    valid = [s for s in scores if s is not None]
    print(f"\n各次得分：{scores}")
    if not valid:
        print("✗ 无有效得分")
        return 1
    mode = max(set(valid), key=valid.count)
    print(f"众数：{mode}")
    if args.expect is not None:
        ok = mode == args.expect
        print(f"预期 {args.expect} → {'✓ 符合' if ok else '✗ 不符'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
