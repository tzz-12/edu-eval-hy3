"""课标「核心素养与学段目标」知识库测试。

覆盖三件事：
1. 整理稿解析正确（条目齐全、来源元数据完整、代码块示例不被误当记录）
2. 初中 9 项核心素养的「三会归属」与「内涵」都入库且可召回
3. 检索函数的**反方向与边界**行为（空查询、查不到、页码缺失）

约定（同项目其他测试）：补反方向用例，方向性 bug 藏在单向测试盲区里。
"""
from __future__ import annotations

import json

import pytest

from edu_eval.eval import dimensions as D
from edu_eval.kb import competency as C

# 初中阶段核心素养的 9 项主要表现，及其「三会」归属
JUNIOR_COMPETENCIES = {
    "抽象能力": "数学眼光",
    "几何直观": "数学眼光",
    "空间观念": "数学眼光",
    "创新意识": "数学眼光",
    "运算能力": "数学思维",
    "推理能力": "数学思维",
    "数据观念": "数学语言",
    "模型观念": "数学语言",
    "应用意识": "数学语言",
}


@pytest.fixture(scope="module")
def entries():
    return C.load_competencies()


# ---------------------------------------------------------------- 解析正确性
def test_entries_loaded(entries):
    """总条数 = 三会 3 + 学段主要表现 2 + 素养—三会归属 9
            + 学段目标 3 + 学业质量标准 4 + 核心素养内涵（9 项素养）9 = 30。"""
    assert len(entries) == 30
    cats = {e.category for e in entries}
    assert cats == {
        "核心素养内涵", "学段主要表现", "素养—三会归属", "学段目标", "学业质量标准",
    }


def test_no_placeholder_entry_from_doc_example(entries):
    """回归：整理稿「记录格式」代码块里的示例不得被解析成真条目。"""
    for e in entries:
        assert "…" not in e.category
        assert e.category != ""
        assert e.text.strip() not in ("", "正文")


def test_all_entries_have_source_meta_and_locator(entries):
    for e in entries:
        assert e.source == "curriculum-2022", e.std_id
        assert e.license and e.version, e.std_id
        assert e.locator.strip(), f"{e.std_id} 缺 locator（章节定位）"


def test_page_is_none_only_when_locator_present(entries):
    """页码不确定的条目（学段目标所在的印刷页未核对）允许 page=None，
    但必须有 locator 兜底，否则无法溯源。"""
    no_page = [e for e in entries if e.page is None]
    assert no_page, "应存在页码待核定的条目，否则本用例失去意义"
    for e in no_page:
        assert e.locator.strip()


def test_only_one_ocr_corrected_entry(entries):
    """仅「学业质量内涵」一条取自本地 OCR 并订正过错字。"""
    corrected = [e for e in entries if e.ocr_corrected]
    assert len(corrected) == 1
    assert corrected[0].category == "学业质量标准"
    assert "主题为载体" in corrected[0].text      # OCR 原文为「主题内载体」


# ---------------------------------------------------------------- 内容正确性
def test_three_aspects_present(entries):
    """三会内涵应有 3 条 aspect 分别为数学眼光/数学思维/数学语言。"""
    aspects = {e.aspect for e in entries if e.category == "核心素养内涵"}
    assert aspects == {"数学眼光", "数学思维", "数学语言"}


def test_nine_competency_definitions_present(entries):
    """9 项核心素养的「内涵」条目均入库（category=核心素养内涵, competency≠空）。"""
    definitions = [e for e in entries
                   if e.category == "核心素养内涵" and e.competency]
    comps = {e.competency for e in definitions}
    assert comps == set(JUNIOR_COMPETENCIES), f"缺项：{set(JUNIOR_COMPETENCIES) - comps}"
    # 9 项 × 1 条内涵 = 9 条
    assert len(definitions) == 9


def test_competency_definitions_are_official_text(entries):
    """9 项素养的内涵应为课标原文逐字（locator 全部指向「表 1」）且 derived=false。"""
    definitions = [e for e in entries
                   if e.category == "核心素养内涵" and e.competency]
    for e in definitions:
        assert "表 1" in e.locator, f"{e.std_id} locator 不指向表 1：{e.locator}"
        assert e.derived is False, f"{e.std_id} 课标原文不应 derived=True"
        assert e.page is not None, f"{e.std_id} 表 1 条目页码应有值"


def test_competency_definitions_cover_keywords(entries):
    """内涵文本的关键动词抽查（防摘抄遗漏）。"""
    definitions = {e.competency: e for e in entries
                   if e.category == "核心素养内涵" and e.competency}
    # 每条素养至少出现 1 个课标原文里的标志性动词/名词
    expected_keywords = {
        "抽象能力": "抽象",
        "运算能力": "运算",
        "几何直观": "直观",
        "空间观念": "空间",
        "推理能力": "推理",
        "数据观念": "数据",
        "模型观念": "模型",
        "应用意识": "应用",
        "创新意识": "创新",
    }
    for comp, kw in expected_keywords.items():
        assert kw in definitions[comp].text, f"{comp} 缺关键词「{kw}」"


def test_primary_and_junior_stage_lists(entries):
    primary = [e for e in entries
               if e.category == "学段主要表现" and e.item_no == "小学"][0]
    junior = [e for e in entries
              if e.category == "学段主要表现" and e.item_no == "初中"][0]
    assert "数感" in primary.text and "推理意识" in primary.text
    assert "推理能力" in junior.text and "推理意识" not in junior.text
    # 初中为 9 项，小学为 11 项
    assert junior.text.count("、") == 8


def test_junior_competencies_complete_and_mapped(entries):
    """9 项素养各有一条归属条目，且三会归属与课标表述一致。"""
    mapping = {e.competency: e.aspect for e in entries
               if e.category == "素养—三会归属"}
    assert mapping == JUNIOR_COMPETENCIES


def test_derived_flag_only_on_assembled_entries(entries):
    """拼接/归纳的条目标记 derived=True；逐字原文条目（含 9 项素养内涵）不得误标。"""
    derived = [e for e in entries if e.derived]
    assert len(derived) == 9                                   # 仅「素养—三会归属」9 条
    assert all(e.category == "素养—三会归属" for e in derived)
    verbatim = [e for e in entries if not e.derived]
    assert len(verbatim) == 21                                 # 21 条 verbatim


def test_junior_stage_goal_and_quality(entries):
    goals = C.by_category(entries, "学段目标")
    assert len(goals) == 3
    assert all(e.stage == "第四学段（7~9年级）" for e in goals)
    assert any("数域扩充" in e.text for e in goals)

    quality = C.by_category(entries, "学业质量标准")
    assert len(quality) == 4
    assert any("四基" in e.text for e in quality)


# ---------------------------------------------------------------- 检索行为
def test_by_competency_exact_then_fuzzy(entries):
    """按 competency 字段精确召回：每项素养至少 1 条（归属 + 内涵可能 2 条）。"""
    hits = C.by_competency(entries, "抽象能力")
    assert len(hits) >= 1
    assert all(e.competency == "抽象能力" for e in hits)
    # 至少含 1 条归属 + 1 条内涵
    cats = {e.category for e in hits}
    assert {"素养—三会归属", "核心素养内涵"}.issubset(cats)

    # 非素养名（正文命中）走模糊路径
    fuzzy = C.by_competency(entries, "数域扩充")
    assert fuzzy and all(e.competency == "" for e in fuzzy)


def test_by_competency_missing_returns_empty(entries):
    """反方向：查不到的素养名必须返回空，不能退化为全量返回。"""
    assert C.by_competency(entries, "不存在的素养") == []


def test_search_empty_query_returns_empty(entries):
    """边界：空查询不得返回全量（否则 Judge 会被灌入无关依据）。"""
    assert C.search(entries, "") == []
    assert C.search(entries, "   ") == []


def test_search_ranks_competency_above_body_mention(entries):
    hits = C.search(entries, "创新意识", limit=3)
    assert hits[0].competency == "创新意识"      # 字段命中优先于正文命中


def test_by_dimension_recall(entries):
    d1 = C.by_dimension(entries, "1")
    assert d1 and all("1" in e.dimensions for e in d1)
    # 维度 8 现在挂在「学业质量标准」+「核心素养内涵（推理/数据/模型/应用）」，
    # 至少应能召回，且包含至少一条非学业质量标准的内涵条目
    d8 = C.by_dimension(entries, "8")
    assert d8
    cats = {e.category for e in d8}
    assert "学业质量标准" in cats
    assert "核心素养内涵" in cats, "9 项素养内涵里至少 4 项把维度 8 列入 dims"


def test_by_dimension_unknown_returns_empty(entries):
    assert C.by_dimension(entries, "99") == []


@pytest.mark.parametrize("limit", [0, 1, 100])
def test_format_for_prompt_respects_limit(entries, limit):
    out = C.format_for_prompt(entries, limit=limit)
    assert out.count("[KB#") == min(limit, len(entries))


# ---------------------------------------------------------------- 构建与校验
def test_build_is_reproducible(tmp_path):
    out = tmp_path / "curriculum_competency.jsonl"
    C.build(out_path=str(out))
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 30
    ids = [json.loads(ln)["std_id"] for ln in lines]
    assert ids == [f"cmp-{i:03d}" for i in range(1, 31)]
    assert ids == sorted(ids)                      # 顺序稳定，便于 diff


def test_build_rejects_entry_without_locator(tmp_path):
    """反方向：缺 locator 的条目必须抛错，不能静默入库。"""
    bad = tmp_path / "bad.md"
    bad.write_text(
        "@ category=核心素养内涵 | aspect=数学眼光 | section=课程目标 | "
        "dims=1 | derived=false | ocr_corrected=false | locator=\n"
        "没有定位信息的正文。\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="locator"):
        C.build(raw_path=str(bad), out_path=str(tmp_path / "out.jsonl"))


def test_build_rejects_empty_text(tmp_path):
    bad = tmp_path / "empty.md"
    bad.write_text(
        "@ category=学段目标 | section=课程目标 | dims=1 | "
        "derived=false | ocr_corrected=false | locator=某章某节\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="无正文"):
        C.build(raw_path=str(bad), out_path=str(tmp_path / "out.jsonl"))


def test_validate_rejects_negative_page():
    e = C.CompetencyEntry(std_id="cmp-x", category="学段目标",
                          text="正文", locator="定位", page=-1)
    assert any("page" in err for err in e.validate())

# --------------------------------------------------------------------------
# 接线（编排层按维度注入 Judge 提示）：dims 标注与 competency_link 的一致性
# --------------------------------------------------------------------------

def test_every_dimension_with_link_recalls_focus_entries():
    """凡评规声明了 competency_link 的维度，按其召回必有 focus 命中。

    反方向保障：若 dims 标注与 competency_link 脱节，Judge 会被要求
    判素养导向却拿不到课标原文（reference-guided 缺口）。
    """
    es = C.load_competencies()
    for dim in D.DIMENSIONS:
        if not dim.competency_link:
            continue
        hits = C.by_dimension(es, dim.id)
        assert hits, f"维度 {dim.id} 声明了素养但知识库无任何条目"
        ranked = C.by_dimension_ranked(hits, dim.id, focus=dim.competency_link)
        focus_set = set(dim.competency_link)
        assert any(e.competency in focus_set or e.aspect in focus_set
                   for e in ranked), (
            f"维度 {dim.id} focus={dim.competency_link} 召回无一命中："
            f"{[e.std_id for e in ranked]}"
        )


def test_dims_tagging_consistent_with_competency_link():
    """dims 标注与 competency_link 逐项对齐：每个 focus 名（素养名或三会
    归属名）的内涵条目必须标注了声明它的维度（否则 focus 排序无从生效）。"""
    es = C.load_competencies()
    for dim in D.DIMENSIONS:
        for name in dim.competency_link:
            defs = [e for e in es
                    if e.category == "核心素养内涵"
                    and (e.competency == name or e.aspect == name)]
            assert defs, f"素养/三会归属 {name!r}（维度 {dim.id}）无内涵条目"
            assert any(dim.id in e.dimensions for e in defs), (
                f"维度 {dim.id} 声明素养 {name!r}，但其内涵条目 dims="
                f"{[e.dimensions for e in defs]} 均未标注 {dim.id}"
            )


def test_dims_without_competency_semantics_have_no_entries():
    """反向边界：维度 2/6/A 无素养语义，知识库不得给它们标 dims（防误注入）。"""
    es = C.load_competencies()
    for dim_id in ("2", "6", "A"):
        assert not C.by_dimension(es, dim_id), (
            f"维度 {dim_id} 不应挂素养条目"
        )


def test_rank_is_deterministic():
    """同一输入永远同一输出（缓存可复现的前提）。"""
    es = C.load_competencies()
    d = D.get_dimension("9")
    a = C.by_dimension_ranked(es, "9", focus=d.competency_link)
    b = C.by_dimension_ranked(es, "9", focus=d.competency_link)
    assert [e.std_id for e in a] == [e.std_id for e in b]


def test_rank_respects_limit():
    es = C.load_competencies()
    assert len(C.by_dimension_ranked(es, "3", focus=None, limit=2)) == 2
