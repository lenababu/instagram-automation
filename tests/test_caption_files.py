from app.scheduler import load_caption_file


def test_load_caption_file_reads_and_trims(tmp_path):
    (tmp_path / "01_X.txt").write_text("  Hook line\n\nBody #tag  \n", encoding="utf-8")
    assert load_caption_file(str(tmp_path), "01_X") == "Hook line\n\nBody #tag"
    assert load_caption_file(str(tmp_path), "02_Y") is None


def test_empty_caption_file_counts_as_missing(tmp_path):
    (tmp_path / "03_Z.txt").write_text("   \n", encoding="utf-8")
    assert load_caption_file(str(tmp_path), "03_Z") is None


from types import SimpleNamespace

from app.scheduler import resolve_caption


def _rec(caption=""):
    return SimpleNamespace(caption=caption)


def _settings(tmp_path, mode="files"):
    return SimpleNamespace(caption_mode=mode, captions_dir=str(tmp_path))


def test_sheet_caption_wins_over_the_file(tmp_path):
    (tmp_path / "05_X.txt").write_text("from the file", encoding="utf-8")
    text, source = resolve_caption(_rec("from the sheet"), _settings(tmp_path), "05_X", False, "")
    assert (text, source) == ("from the sheet", "sheet")


def test_file_is_used_when_the_row_has_no_caption(tmp_path):
    (tmp_path / "05_X.txt").write_text("from the file", encoding="utf-8")
    text, source = resolve_caption(_rec(""), _settings(tmp_path), "05_X", False, "")
    assert (text, source) == ("from the file", "file")


def test_missing_everywhere_tells_the_caller_to_skip(tmp_path):
    text, source = resolve_caption(_rec(""), _settings(tmp_path), "29_New", False, "")
    assert text is None and source == "missing"


def test_ai_mode_defers_generation_only_when_nothing_is_written(tmp_path):
    assert resolve_caption(_rec(""), _settings(tmp_path, "ai"), "29_New", False, "") == (None, "ai")
    # even in ai mode, a human caption on the row is never regenerated
    assert resolve_caption(_rec("mine"), _settings(tmp_path, "ai"), "29_New", False, "") == ("mine", "sheet")


def test_interrupted_run_reuses_its_caption(tmp_path):
    text, source = resolve_caption(_rec("half done"), _settings(tmp_path), "05_X", True, "Analysed")
    assert (text, source) == ("half done", "interrupted")
