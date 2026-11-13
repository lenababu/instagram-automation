from app.sheets_service import COLUMNS, PostRecord, SheetsService
from app.config import Settings


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = rows or []

    def row_values(self, i):
        return self.rows[i - 1] if len(self.rows) >= i else []

    def get_all_values(self):
        return self.rows

    def append_row(self, values, value_input_option=None):
        self.rows.append(values)

    def update(self, range_name, values, value_input_option=None):
        row = int("".join(ch for ch in range_name if ch.isdigit()))
        while len(self.rows) < row:
            self.rows.append([])
        self.rows[row - 1] = values[0]


def test_header_created_on_empty_sheet():
    ws = FakeWorksheet()
    SheetsService(Settings(), worksheet=ws)
    assert ws.rows[0] == COLUMNS


def test_old_header_extended_without_touching_data():
    ws = FakeWorksheet([COLUMNS[:11], ["001.mp4", "a", "", "", "Published", "", "", "m", "", "", "0"]])
    svc = SheetsService(Settings(), worksheet=ws)
    assert ws.rows[0] == COLUMNS
    recs = svc.all_records()
    assert recs[0].drive_file_id == "a" and recs[0].status == "Published" and recs[0].ig_container_id == ""


def test_upsert_append_then_update():
    ws = FakeWorksheet()
    svc = SheetsService(Settings(), worksheet=ws)
    r = PostRecord(filename="001.mp4", drive_file_id="a")
    svc.upsert(r)
    assert r.row_number == 2
    r.status = "Published"
    svc.upsert(r)
    assert len(ws.rows) == 2 and ws.rows[1][4] == "Published"


def test_round_trip():
    r = PostRecord(filename="x.mp4", drive_file_id="id", retry_count=2, caption="hi\nthere")
    back = PostRecord.from_row([str(v) for v in r.to_row()], 5)
    assert back.retry_count == 2 and back.caption == "hi\nthere" and back.row_number == 5
