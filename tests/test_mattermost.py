"""Mattermost REST client: API mode (posts/threads/edits), webhook mode, errors."""
import pytest
import requests

from berend import config
from berend.mattermost import Mattermost


class Resp:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data


@pytest.fixture
def mm(monkeypatch):
    m = Mattermost()
    m.calls = []

    def rec(method):
        def f(url, json=None, timeout=None):
            m.calls.append((method, url, json))
            return m.next.pop(0) if m.next else Resp({"id": "p1"})
        return f

    m.next = []
    monkeypatch.setattr(m.s, "post", rec("POST"))
    monkeypatch.setattr(m.s, "put", rec("PUT"))
    monkeypatch.setattr(m.s, "get", lambda url, timeout=None: m.next.pop(0))
    return m


def test_api_mode_posts_to_the_channel_with_auth(mm):
    assert mm.api and mm.s.headers["Authorization"] == "Bearer x"
    assert mm.post("hi", attachments=[{"color": "#fff"}], root_id="r0") == "p1"
    method, url, body = mm.calls[0]
    assert url == "https://mm.test/api/v4/posts"
    assert body == {"channel_id": "alerts", "message": "hi", "props": {"attachments": [{"color": "#fff"}]},
                    "root_id": "r0"}
    mm.post("x", channel_id="other")
    assert mm.calls[1][2]["channel_id"] == "other" and "root_id" not in mm.calls[1][2]


def test_update_patches_and_skips_without_id(mm):
    mm.update("p9", "edited")
    assert mm.calls == [("PUT", "https://mm.test/api/v4/posts/p9/patch",
                         {"message": "edited", "props": {"attachments": []}})]
    mm.update(None, "x")
    assert len(mm.calls) == 1


def test_errors_are_logged_and_counted_not_raised(mm, caplog):
    mm.next = [Resp(status=500)]
    assert mm.post("x") is None
    mm.next = [Resp(status=403)]
    mm.update("p1", "x")
    assert "mattermost post failed" in caplog.text and "mattermost update failed" in caplog.text


def test_webhook_mode(monkeypatch):
    monkeypatch.setattr(config, "MATTERMOST_TOKEN", "")
    monkeypatch.setattr(config, "MATTERMOST_WEBHOOK_URL", "https://mm.test/hooks/abc")
    monkeypatch.setattr(config, "MATTERMOST_ICON_URL", "https://icon")
    m = Mattermost()
    sent = []
    monkeypatch.setattr(m.s, "post", lambda url, json, timeout: sent.append((url, json)) or Resp())
    assert m.post("alert") is None                      # webhooks return no post id
    assert sent == [("https://mm.test/hooks/abc", {"text": "alert", "attachments": [],
                                                    "username": config.MATTERMOST_USERNAME,
                                                    "icon_url": "https://icon"})]
    m.update("p1", "x")                                  # no API: nothing to edit
    assert len(sent) == 1


def test_me_and_thread_order(mm):
    mm.next = [Resp({"id": "bot", "username": "berend"})]
    assert mm.me()["username"] == "berend"
    mm.next = [Resp({"posts": {"b": {"id": "b", "create_at": 2}, "a": {"id": "a", "create_at": 1}}})]
    assert [p["id"] for p in mm.thread("a")] == ["a", "b"]
    mm.next = [Resp({})]
    assert mm.thread("a") == []
