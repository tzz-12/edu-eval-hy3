"""项目路径解析（P0-10 · G）：知识库与缓存不再依赖当前工作目录。

背景：早期实现直接用相对路径 "data/kb/knowledge.jsonl" 与
"results/.cache/judge"。从仓库外（如 /tmp）运行 CLI 时这些路径不存在，
`load_kb_assets()` 的三个 `except Exception: ... = None` 会把失败静默吞掉，
于是规则层降级、年级映射失效，而报告照常输出 PASS —— 一个不声不响的错误结果。

约定：
- 相对路径优先按**项目根目录**（仓库根）解析，其次按当前目录；
- 数据目录可用环境变量 EDU_EVAL_DATA_DIR 覆盖（便于自定义知识库位置）；
- 缓存目录可用环境变量 EDU_EVAL_CACHE_DIR 覆盖。
"""
from __future__ import annotations

import os
from typing import List, Optional

#: 数据目录下的相对路径（相对项目根）
KB_DIR = os.path.join("data", "kb")
KB_JSONL = os.path.join(KB_DIR, "knowledge.jsonl")
KB_DB = os.path.join(KB_DIR, "knowledge.db")
GRADE_JSON = os.path.join(KB_DIR, "concept_grade.json")
CURRICULUM_JSONL = os.path.join(KB_DIR, "curriculum_junior.jsonl")

#: 课标「核心素养与学段目标」条目（curriculum_junior 只覆盖内容要求，
#: 核心素养章不在其中，故单独建一份）。
#: 人工整理稿入库（可审计），产出的 jsonl 在 data/kb/ 下、随脚本重建。
COMPETENCY_MD = os.path.join("data", "curriculum", "curriculum_competency.md")
COMPETENCY_JSONL = os.path.join(KB_DIR, "curriculum_competency.jsonl")

#: Tier 2 可核验断言集目录（P1-1）。
#: 与 data/kb/ 分开存放是刻意的：data/kb/ 装的是 K12-KGraph 衍生物
#: （CC BY-NC-SA，不入库），而 Tier 2 断言为本项目自建（MIT），
#: 必须入库以便复现与审计。
ASSERTIONS_DIR = os.path.join("data", "assertions")

CACHE_DIR = os.path.join("results", ".cache", "judge")


def project_root() -> str:
    """仓库根目录：src/edu_eval/paths.py → 上溯三级。"""
    here = os.path.dirname(os.path.abspath(__file__))  # .../src/edu_eval
    src = os.path.dirname(here)                        # .../src
    return os.path.dirname(src)                        # 仓库根


def data_dir() -> str:
    env = (os.getenv("EDU_EVAL_DATA_DIR") or "").strip()
    return env or os.path.join(project_root(), "data")


def cache_dir() -> str:
    env = (os.getenv("EDU_EVAL_CACHE_DIR") or "").strip()
    return env or os.path.join(project_root(), CACHE_DIR)


def resolve(rel: str, *, extra_roots: Optional[List[str]] = None) -> str:
    """把相对路径解析为绝对路径。

    解析顺序（第一个命中的胜出）：
      1. 绝对路径 → 原样返回
      2. 环境变量 EDU_EVAL_DATA_DIR 之下（仅当 rel 以 data/ 开头；
         **显式设置即为权威覆盖**，不回落项目根 —— 否则无法用它隔离
         测试环境，缺失场景的告警也无法触发）
      3. 项目根目录之下
      4. 当前工作目录之下
    都不存在时，返回「项目根目录之下」的路径，便于报错信息保持一致。
    """
    if os.path.isabs(rel):
        return rel
    env_set = bool((os.getenv("EDU_EVAL_DATA_DIR") or "").strip())
    if env_set and (rel.startswith("data" + os.sep) or rel.startswith("data/")):
        # 权威覆盖：不再尝试项目根 / 当前目录
        return os.path.join(data_dir(), rel.split(os.sep, 1)[-1]
                            if os.sep in rel else rel)
    candidates: List[str] = [os.path.join(project_root(), rel)]
    for root in (extra_roots or []):
        candidates.append(os.path.join(root, rel))
    candidates.append(os.path.abspath(rel))
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join(project_root(), rel)


def kb_jsonl() -> str:
    return resolve(KB_JSONL)


def kb_db() -> str:
    return resolve(KB_DB)


def grade_json() -> str:
    return resolve(GRADE_JSON)


def curriculum_jsonl() -> str:
    """课标内容要求（160 条，Tier 2 断言的 std_ref 依据）。"""
    return resolve(CURRICULUM_JSONL)


def competency_md() -> str:
    """核心素养整理稿（入库，人工可审计）。"""
    return resolve(COMPETENCY_MD)


def competency_jsonl() -> str:
    """核心素养条目（由整理稿构建，不入库）。"""
    return resolve(COMPETENCY_JSONL)


def assertions_dir() -> str:
    return resolve(ASSERTIONS_DIR)


def assertion_files() -> List[str]:
    """断言集目录下的全部 *.jsonl（按文件名排序，保证装载顺序稳定）。"""
    d = assertions_dir()
    if not os.path.isdir(d):
        return []
    out = [os.path.join(d, n) for n in sorted(os.listdir(d))
           if n.endswith(".jsonl")]
    return out
