"""PDF 抽取降噪（parse.layout.denoise_text）的回归测试。

三条规则各有判据，测试覆盖：
- 词内断裂（单空格）→ 删除
- 栏位分隔（两格以上）→ 归一为全角空格，且**不**被误并成词
- 边界：中文与数字/字母之间的空格、合法叠词、纯英文不动
- parse_file 在 .md/.txt 与 PDF 路径上的降噪开关
"""
import os
import tempfile

from edu_eval.parse.layout import denoise_text, noise_stats
from edu_eval.parse.parsers import parse_file


def test_intra_word_gap_removed():
    # 词内被切断的噪声（单空格）应删除
    assert denoise_text("教学过 程") == "教学过程"
    assert denoise_text("对 于边长") == "对于边长"
    assert denoise_text("降 低研究难度") == "降低研究难度"


def test_column_gap_preserved_as_fullwidth():
    # 两格以上 = 表单/栏位分隔，归一为全角空格，结构保留
    out = denoise_text("学  科  数学　教师姓名  韩琰")
    assert "学　科　数学" in out
    assert "教师姓名" in out
    # 全角空格保留，没有被合并成「学科数学教师姓名韩琰」
    assert "学科数学教师姓名" not in out


def test_column_gap_not_merged_into_word():
    # 栏位分隔必须保留，不能跨栏并成词
    out = denoise_text("学校  班级  姓名")
    assert out == "学校　班级　姓名"


def test_blank_run_collapsed():
    assert denoise_text("段一\n\n\n\n段二") == "段一\n\n段二"
    assert denoise_text("a\n\n\n\n\n\nb") == "a\n\nb"


def test_cjk_number_space_untouched():
    # 中文与数字/字母之间的空格可能是单位/公式排版，不能删
    assert denoise_text("360 °") == "360 °"
    assert denoise_text("约 3 小时") == "约 3 小时"
    assert denoise_text("图 1 所示") == "图 1 所示"


def test_legal_reduplication_untouched():
    # 降噪不做「去重字」，合法叠词「常常」「刚刚」「从从」的字符本身不被破坏
    out = denoise_text("常常 刚刚 练习 从从 简单出发")
    assert "常常" in out and "刚刚" in out and "从从" in out
    # 注：CJK 之间的单空格属词内断裂规则，会被删（"常常 刚刚"→"常常刚刚"），
    # 这是有意的取舍，此处只验证叠字字符未被误删。


def test_english_untouched():
    assert denoise_text("Hello world foo bar") == "Hello world foo bar"
    # 英文单词之间的单空格不在 CJK 边界，不动
    assert denoise_text("a b c 中文") == "a b c 中文"


def test_noise_stats_counts_intra_word_gaps():
    noisy = "教学过 程 对 于边长 降 低"
    clean = denoise_text(noisy)
    b, a = noise_stats(noisy), noise_stats(clean)
    assert b["intra_word_gaps"] >= 5
    assert a["intra_word_gaps"] == 0


def test_parse_file_md_denoise_on():
    text = "教学目标\n\n教学过 程 设计 对 于边长 说明\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        doc = parse_file(path, denoise=True)
        assert "教学过 程" not in doc.text
        assert "教学过程" in doc.text
        assert "对于边长" in doc.text
        # 默认 .md 路径不动（denoise=False）
        doc2 = parse_file(path, denoise=False)
        assert "教学过 程" in doc2.text
    finally:
        os.unlink(path)


def test_parse_file_txt_denoise_on():
    text = "学  科  数学　教师姓名  韩琰\n"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        doc = parse_file(path, denoise=True)
        # 栏位分隔保留为全角空格
        assert "学　科　数学" in doc.text
        assert "学科数学" not in doc.text
    finally:
        os.unlink(path)
