import pytest

from app.config import Settings
from app.instagram_publisher import InstagramPublisher, InstagramError, RateLimited, TokenExpired


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self._p


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, params=None, data=None, timeout=None):
        self.calls.append((method, url.split("/v24.0/")[1], params or data))
        return FakeResp(self.script.pop(0))


def make(script):
    s = Settings(meta_access_token="tok", instagram_user_id="123", meta_api_version="v24.0")
    sess = FakeSession(script)
    return InstagramPublisher(s, session=sess), sess


def test_full_publish_flow(monkeypatch):
    monkeypatch.setattr("app.instagram_publisher.time.sleep", lambda s: None)
    pub, sess = make([
        {"id": "c1"},
        {"status_code": "IN_PROGRESS"},
        {"status_code": "FINISHED"},
        {"id": "m1"},
        {"permalink": "https://www.instagram.com/reel/abc/"},
    ])
    result, cid = pub.publish_reel("https://v.mp4", "caption", "https://t.jpg")
    assert cid == "c1" and result.media_id == "m1" and "reel/abc" in result.permalink
    first = sess.calls[0]
    assert first[1] == "123/media" and first[2]["media_type"] == "REELS"
    assert first[2]["cover_url"] == "https://t.jpg" and first[2]["share_to_feed"] == "true"
    assert "access_token" in first[2]


def test_token_expired_detected():
    pub, _ = make([{"error": {"code": 190, "error_subcode": 463, "message": "expired"}}])
    with pytest.raises(TokenExpired):
        pub.container_status("c")


def test_rate_limit_detected():
    pub, _ = make([{"error": {"code": 4, "message": "limit"}}])
    with pytest.raises(RateLimited):
        pub.container_status("c")


def test_processing_error_raises():
    pub, _ = make([{"status_code": "ERROR", "status": "bad video"}])
    with pytest.raises(InstagramError):
        pub.wait_until_ready("c", timeout_s=1, poll_s=0)


def test_publish_error_checks_actual_state_before_failing(monkeypatch):
    monkeypatch.setattr("app.instagram_publisher.time.sleep", lambda s: None)
    pub, _ = make([
        {"status_code": "FINISHED"},
        {"status_code": "FINISHED"},
        {"error": {"code": 9007, "message": "ambiguous"}},
        {"data": [{"id": "m9", "caption": "my caption here", "permalink": "https://ig/x"}]},
    ])
    result, cid = pub.publish_reel("u", "my caption here", None, existing_container="c-old")
    assert result.media_id == "m9" and cid == "c-old"


def test_transient_network_errors_are_retried_then_succeed(monkeypatch):
    """A dropped connection must not fail the post; _call retries up to three times."""
    import requests as _rq

    from app import instagram_publisher as ip
    monkeypatch.setattr(ip.time, "sleep", lambda *_: None)  # no real backoff in tests

    class FlakySession:
        def __init__(self):
            self.attempts = 0

        def request(self, method, url, params=None, data=None, timeout=None):
            self.attempts += 1
            if self.attempts < 3:
                raise _rq.ConnectionError("connection reset")
            return FakeResp({"id": "123", "username": "ligenixai"})

    s = Settings(meta_access_token="tok", instagram_user_id="123", meta_api_version="v24.0")
    sess = FlakySession()
    pub = InstagramPublisher(s, session=sess)
    assert pub._call("GET", "123")["username"] == "ligenixai"
    assert sess.attempts == 3


def test_network_error_gives_up_after_three_attempts(monkeypatch):
    import requests as _rq

    from app import instagram_publisher as ip
    monkeypatch.setattr(ip.time, "sleep", lambda *_: None)

    class DeadSession:
        def __init__(self):
            self.attempts = 0

        def request(self, *a, **kw):
            self.attempts += 1
            raise _rq.ConnectionError("no route to host")

    s = Settings(meta_access_token="tok", instagram_user_id="123", meta_api_version="v24.0")
    sess = DeadSession()
    with pytest.raises(InstagramError, match="Network error"):
        InstagramPublisher(s, session=sess)._call("GET", "123")
    assert sess.attempts == 3
