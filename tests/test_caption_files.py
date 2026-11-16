from app.scheduler import load_caption_file


def test_load_caption_file_reads_and_trims(tmp_path):
    (tmp_path / "01_X.txt").write_text("  Hook line\n\nBody #tag  \n", encoding="utf-8")
    assert load_caption_file(str(tmp_path), "01_X") == "Hook line\n\nBody #tag"
    assert load_caption_file(str(tmp_path), "02_Y") is None


def test_empty_caption_file_counts_as_missing(tmp_path):
    (tmp_path / "03_Z.txt").write_text("   \n", encoding="utf-8")
    assert load_caption_file(str(tmp_path), "03_Z") is None
