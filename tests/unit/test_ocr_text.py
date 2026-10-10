from stockagent.analysis.ocr_text import compact_ocr_text


def test_compact_ocr_text_removes_empty_and_duplicate_lines_without_guessing():
    text = "持仓股\n\n\n持仓股\n市值\n\n盈亏"

    assert compact_ocr_text(text) == "持仓股 · 市值 · 盈亏"


def test_compact_ocr_text_preserves_visible_fragments():
    assert compact_ocr_text("世茂\n源\n10700\n24.940") == "世茂 · 源 · 10700 · 24.940"
