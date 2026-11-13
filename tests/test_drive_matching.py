from app.drive_service import DriveFile, match_thumbnails, sort_naturally


def f(name, fid=None):
    return DriveFile(file_id=fid or f"id-{name}", name=name)


def test_natural_sort_order():
    files = [f("010.mp4"), f("2.mp4"), f("001.mp4"), f("003.MOV"), f("1a.mp4")]
    assert [x.name for x in sort_naturally(files)] == ["001.mp4", "1a.mp4", "2.mp4", "003.MOV", "010.mp4"]


def test_thumbnail_matching_by_stem():
    videos = [f("001.mp4"), f("002.mov"), f("003.mp4")]
    thumbs = [f("001.jpg"), f("003.PNG"), f("004.jpg")]
    cands = match_thumbnails(videos, thumbs)
    assert cands[0].thumbnail.name == "001.jpg"
    assert cands[1].thumbnail is None  # missing thumbnail detected
    assert cands[2].thumbnail.name == "003.PNG"


def test_prefers_jpg_over_png():
    cands = match_thumbnails([f("001.mp4")], [f("001.png"), f("001.jpg")])
    assert cands[0].thumbnail.name == "001.jpg"


def test_extension_and_stem():
    d = f("My Video 12.MP4")
    assert d.stem == "My Video 12"
    assert d.extension == ".mp4"
