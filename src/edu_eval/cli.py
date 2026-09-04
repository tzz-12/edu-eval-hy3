"""命令行入口：edu-eval <文件> [--grade ...] [--json] [--mock]"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import Hy3Config
from .hy3 import QuotaExceededError
from .eval import dimensions as D
from .eval.knowledge_base import KnowledgeBase
from .eval.run_eval import EvalContext, evaluate


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edu-eval",
        description="EduEval —— 基于混元 Hy3 的初中数学教学设计质量评估器（个人/活动作品）。",
    )
    p.add_argument("path", help="待评估文件（.md/.txt/.docx/.pdf/.pptx）")
    p.add_argument("--grade", help="声明目标年级，例如 七年级")
    p.add_argument("--version", help="声明教材版本，例如 人教版")
    p.add_argument("--topic", help="声明课题，例如 一元一次方程")
    p.add_argument("--period", help="声明课时，例如 1课时")
    p.add_argument("--kb", help="知识库 JSONL 路径（可选）")
    p.add_argument("--json", action="store_true", help="以 JSON 形式输出结果")
    p.add_argument("--mock", action="store_true", help="演示模式：不连接 Hy3，返回占位结果")
    p.add_argument("--denoise", action="store_true",
                   help="对文本类输入（.md/.txt）额外跑 PDF 抽取降噪；"
                        "PDF 路径默认降噪，无需此开关")
    p.add_argument("--out", help="将报告写入该路径（.txt 或 .json）")
    p.add_argument("--no-dual-sample", action="store_true",
                   help="关闭双采样自一致（默认开启：每个 Judge 两次视角采样，"
                        "分差 ≤ 阈值取平均、否则层内仲裁）。关闭后每个 Judge 只调一次，"
                        "更快更省，但分数不可复现性不再被度量")
    p.add_argument("--dual-threshold", type=int, default=None,
                   help="双采样分差阈值：|Δ| ≤ 该值取平均，超过则层内仲裁（默认 1）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    D.validate_weights()

    if args.mock:
        os.environ["HY3_MOCK"] = "1"
    cfg = Hy3Config.from_env(require_key=not args.mock)

    # 未指定 --kb 时传 None：由 evaluate() 统一装载知识库 + 年级映射 + 检索器。
    # 此前传空 KnowledgeBase 会让 evaluate 走「用户已给 kb」分支，
    # 年级映射与检索器被硬置 None，规则层静默失效（P0-10 · G）。
    kb = KnowledgeBase.load(args.kb) if args.kb else None
    ctx = EvalContext(grade=args.grade or "", version=args.version or "",
                      topic=args.topic or "", period=args.period or "")

    if not os.path.exists(args.path):
        print(f"文件不存在：{args.path}", file=sys.stderr)
        return 2

    if args.dual_threshold is not None and args.dual_threshold < 0:
        print("--dual-threshold 不能为负数", file=sys.stderr)
        return 2

    try:
        report = evaluate(args.path, cfg, ctx, kb, denoise=args.denoise,
                          dual_sample=not args.no_dual_sample,
                          **({"dual_threshold": args.dual_threshold}
                             if args.dual_threshold is not None else {}))
    except QuotaExceededError as e:
        # 全局额度故障：报告此时毫无意义，明确报错并给出可操作建议
        print(f"评测中止（额度/限流耗尽，未产出报告）：\n{e}", file=sys.stderr)
        print("建议：等待额度重置后重跑，或改用不受该限制的模型/密钥。",
              file=sys.stderr)
        return 3

    if args.json:
        out = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    else:
        out = report.to_text()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"报告已写入：{args.out}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
