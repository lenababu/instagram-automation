import pytest

from app.config import Settings
from app.facebook_publisher import FacebookError, FacebookPublisher


class FakeResponse:
    def __init__(self, payload, content=b"{}"):
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class FakeSession:
    """Replays queued responses and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append((method, url, params or data or {}))
        return FakeResponse(self.responses.pop(0))

    def post(self, url, headers=None, timeout=None):
        self.calls.append(("UPLOAD", url, headers or {}))
        return FakeResponse(self.responses.pop(0))


def settings(**kw):
    return Settings(meta_access_token="t", meta_api_version="v24.0", **kw)


def test_publish_reel_runs_the_three_phases_and_returns_permalink():
    s = FakeSession([
        {"data": [{"id": "PAGE1", "name": "Ligenix ai", "access_token": "ptok"}]},
        {"video_id": "VID1", "upload_url": "https://rupload.example/VID1"},
        {"success": True},
        {},                                             # finish
        {"status": {"video_status": "ready"}},
        {"permalink_url": "/reel/123"},
    ])
    fb = FacebookPublisher(settings(), session=s)
    post = fb.publish_reel("https://cdn.example/v.mp4", "hello")

    assert post.video_id == "VID1"
    assert post.permalink == "https://www.facebook.com/reel/123"
    phases = [c[2].get("upload_phase") for c in s.calls if isinstance(c[2], dict)]
    assert "start" in phases and "finish" in phases
    upload = next(c for c in s.calls if c[0] == "UPLOAD")
    assert upload[2]["file_url"] == "https://cdn.example/v.mp4"
    assert upload[2]["Authorization"] == "OAuth ptok"


def test_page_prefers_the_configured_page_id():
    s = FakeSession([{"data": [
        {"id": "A", "name": "Other", "access_token": "ta"},
        {"id": "B", "name": "Wanted", "access_token": "tb"},
    ]}])
    page_id, token = FacebookPublisher(settings(facebook_page_id="B"), session=s).page()
    assert (page_id, token) == ("B", "tb")


def test_missing_page_is_an_error():
    s = FakeSession([{"data": []}])
    with pytest.raises(FacebookError):
        FacebookPublisher(settings(), session=s).page()


def test_has_permission_detects_pages_manage_posts():
    granted = {"data": [{"permission": "pages_manage_posts", "status": "granted"}]}
    assert FacebookPublisher(settings(), session=FakeSession([granted])).has_permission()
    declined = {"data": [{"permission": "pages_manage_posts", "status": "declined"}]}
    assert not FacebookPublisher(settings(), session=FakeSession([declined])).has_permission()


def test_graph_error_becomes_facebook_error():
    s = FakeSession([{"error": {"code": 200, "message": "no permission"}}])
    with pytest.raises(FacebookError, match="no permission"):
        FacebookPublisher(settings(), session=s).page()
