from app.drive_service import DriveFile, VideoCandidate
from app.duplicate_checker import select_next
from app.sheets_service import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_READY,
    STATUS_SKIPPED,
    PostRecord,
)


def cand(name, fid, thumb=True):
    v = DriveFile(file_id=fid, name=name)
    t = DriveFile(file_id=f"t-{fid}", name=name.rsplit(".", 1)[0] + ".jpg") if thumb else None
    return VideoCandidate(v, t)


def rec(fid, status, retries=0, media_id=""):
    return PostRecord(filename=fid, drive_file_id=fid, status=status, retry_count=retries,
                      instagram_media_id=media_id, row_number=2)


def test_picks_first_unposted_in_order():
    cands = [cand("001.mp4", "a"), cand("002.mp4", "b"), cand("003.mp4", "c")]
    records = {"a": rec("a", STATUS_PUBLISHED, media_id="m1")}
    sel = select_next(cands, records)
    assert sel.candidate.video.file_id == "b"
    assert sel.record.thumbnail_file_id == "t-b"
    assert not sel.resumed


def test_never_republishes_even_if_status_wrong():
    cands = [cand("001.mp4", "a"), cand("002.mp4", "b")]
    records = {"a": rec("a", STATUS_FAILED, media_id="17890")}  # media id present => published
    assert select_next(cands, records).candidate.video.file_id == "b"


def test_skipped_is_terminal():
    cands = [cand("001.mp4", "a")]
    assert select_next(cands, {"a": rec("a", STATUS_SKIPPED)}) is None


def test_resumes_interrupted_job_first():
    cands = [cand("001.mp4", "a"), cand("002.mp4", "b"), cand("003.mp4", "c")]
    records = {"a": rec("a", STATUS_PUBLISHED, media_id="x"), "c": rec("c", STATUS_READY)}
    sel = select_next(cands, records)
    assert sel.candidate.video.file_id == "c"
    assert sel.resumed


def test_gives_up_after_max_retries():
    cands = [cand("001.mp4", "a"), cand("002.mp4", "b")]
    records = {"a": rec("a", STATUS_PROCESSING, retries=3)}
    sel = select_next(cands, records)
    assert sel.candidate.video.file_id == "b"


def test_retries_failed_video():
    cands = [cand("001.mp4", "a")]
    sel = select_next(cands, {"a": rec("a", STATUS_FAILED, retries=1)})
    assert sel.candidate.video.file_id == "a"


def test_nothing_left():
    cands = [cand("001.mp4", "a")]
    assert select_next(cands, {"a": rec("a", STATUS_PUBLISHED, media_id="m")}) is None


def test_identity_is_file_id_not_filename():
    # Same filename re-uploaded as a new Drive file => new file id => eligible
    cands = [cand("001.mp4", "new-id")]
    records = {"old-id": PostRecord(filename="001.mp4", drive_file_id="old-id", status=STATUS_PUBLISHED, instagram_media_id="m")}
    assert select_next(cands, records).candidate.video.file_id == "new-id"
