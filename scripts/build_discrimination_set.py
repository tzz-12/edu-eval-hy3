"""生成判别力实验样本集（base / mild / severe 三档对照）。

设计依据（DESIGN.md §4.4）
--------------------------
教学缺陷判别力要求：对已通过知识准入的样本注入非知识类教学缺陷，
缺陷越严重，目标维度分与总分应单调下降，且证据定位到注入片段。

因此每个目标维度需要三档：
- base   ：干净底稿，无缺陷
- mild   ：轻度缺陷 —— 「形似神不似」，环节还在但无实质（能否识破表面包装）
- severe ：重度缺陷 —— 直接缺失或退化为空话

轻度档才是判别力的真正考验：重度缺失谁都看得出来，
轻度包装才是 AI 生成课件最典型的失效模式。

用法
----
    python scripts/build_discrimination_set.py

产出
----
    data/samples/discrimination/*.md        11 份样本（1 base + 5 维 × 2 档）
    data/samples/discrimination/manifest.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = ROOT / "data" / "testsets" / "discrimination_cases.json"
OUT_DIR = ROOT / "data" / "samples" / "discrimination"


def main() -> int:
    spec = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    base_path = ROOT / spec["base_doc"]
    if not base_path.exists():
        print(f"✗ 底稿不存在：{base_path}")
        return 1

    base_text = base_path.read_text(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 对照组：干净底稿
    (OUT_DIR / "base.md").write_text(base_text, encoding="utf-8")
    manifest = {
        "base_doc": spec["base_doc"],
        "base_meta": spec.get("base_meta", {}),
        "cases": [],
    }

    ok = True
    for c in spec["cases"]:
        # 一个用例可注入多段：高质量教案的维度证据是分布式的，
        # 只改一处往往不足以让该维度真正失效。
        patches = c.get("patches") or [{"old": c["old"], "new": c["new"]}]
        text = base_text
        failed = False
        for i, p in enumerate(patches, 1):
            old, new = p["old"], p["new"]
            if old not in text:
                print(f"✗ {c['case_id']} 第 {i}/{len(patches)} 段：锚点未命中，"
                      f"注入会静默失败\n    锚点前 60 字：{old[:60]}")
                failed = True
                break
            if old == new:
                print(f"✗ {c['case_id']} 第 {i} 段：old 与 new 相同，等于没注入")
                failed = True
                break
            before = text
            text = text.replace(old, new, 1)
            if text == before:
                print(f"✗ {c['case_id']} 第 {i} 段：替换后文本未变化")
                failed = True
                break
        if failed:
            ok = False
            continue
        if text == base_text:
            print(f"✗ {c['case_id']}：全部注入后文本未变化")
            ok = False
            continue

        out = OUT_DIR / f"{c['case_id']}.md"
        out.write_text(text, encoding="utf-8")
        manifest["cases"].append({
            "case_id": c["case_id"],
            "dim": c["dim"],
            "level": c["level"],
            "type": c["type"],
            "desc": c["desc"],
            "file": str(out.relative_to(ROOT)),
            "chars": len(text),
        })
        print(f"✓ {c['case_id']:16s} 维度{c['dim']} {c['level']:7s} "
              f"{len(text):5d} 字  {c['desc'][:34]}")

    if not ok:
        print("\n✗ 有用例注入失败，已中止（不产出残缺样本集）")
        return 1

    mf = OUT_DIR / "manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    dims = sorted({c["dim"] for c in manifest["cases"]})
    print(f"\n产出 {len(manifest['cases'])} 个注入样本 + 1 份 base 对照")
    print(f"覆盖维度：{', '.join(dims)}")
    print(f"清单：{mf.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
