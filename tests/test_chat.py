import json
import urllib.request

import pytest

from berend import agent, config, health, mattermost_bot, slack_bot
from berend.metrics import heartbeat


# --- Slack formatting -------------------------------------------------------
def test_mrkdwn_conversion_keeps_code():
    out = slack_bot.to_mrkdwn("**Cause**: see [docs](https://x/y)\n- step\n```\n**keep**\n```")
    assert out == "*Cause*: see <https://x/y|docs>\n• step\n```\n**keep**\n```"


def test_split_never_breaks_code_fences():
    parts = slack_bot._split("```\n" + "line\n" * 1500 + "```")
    assert len(parts) > 1 and all(p.count("```") % 2 == 0 for p in parts)


def test_blocks_have_sources_footer_and_feedback():
    ans = agent.Answer("Answer", [("payments.md", "https://x/p")], ["search_knowledge_base"], 2.5)
    blocks = slack_bot.build_blocks(ans, "q")
    json.dumps(blocks)
    assert [b["type"] for b in blocks] == ["section", "context", "context", "actions"]
    assert "Berend" in blocks[2]["elements"][0]["text"] and "📚 docs" in blocks[2]["elements"][0]["text"]


# --- Mattermost chat --------------------------------------------------------
class FakeMM:
    api = True

    def __init__(self, thread=None):
        self.posts, self.updates, self._thread = [], [], thread or []

    def me(self):
        return {"id": "BOT", "username": "berend"}

    def thread(self, root_id):
        return self._thread

    def post(self, text="", attachments=None, root_id=None, channel_id=None):
        self.posts.append({"text": text, "root": root_id, "channel": channel_id})
        return f"p{len(self.posts)}"

    def update(self, post_id, text="", attachments=None):
        self.updates.append((post_id, text))


class SyncPool:
    def submit(self, fn, *a):
        fn(*a)


def posted(message, user="U1", channel_type="O", mentions=("BOT",), root_id="", pid="m1"):
    return {"event": "posted", "data": {
        "channel_type": channel_type, "mentions": json.dumps(list(mentions)),
        "post": json.dumps({"id": pid, "user_id": user, "channel_id": "C1", "message": message,
                            "root_id": root_id, "props": {}})}}


@pytest.fixture
def bot():
    b = mattermost_bot.MattermostBot(FakeMM())
    b.pool = SyncPool()
    return b


def test_mattermost_answers_mentions_in_thread_with_footer(bot, fake_llm):
    fake_llm.script = ["Berend here."]
    bot.on_event(posted("@berend who are you?"))
    placeholder = bot.mm.posts[0]
    assert placeholder["root"] == "m1" and placeholder["channel"] == "C1"
    final = bot.mm.updates[-1][1]
    assert final.startswith("Berend here.") and "_Berend · ⏱" in final
    assert fake_llm.rounds[0][-1]["content"] == "who are you?"


def test_mattermost_ignores_own_posts_and_unmentioned(bot, fake_llm):
    bot.on_event(posted("@berend hi", user="BOT"))
    bot.on_event(posted("just chatting", mentions=()))
    assert bot.mm.posts == []


def test_mattermost_followup_in_thread_and_alert_context(bot, fake_llm):
    alert = {"id": "a1", "user_id": "BOT", "create_at": 1, "message": "",
             "props": {"attachments": [{"title": "🔴 payments-api — Degraded", "text": "ImagePullBackOff",
                                        "fields": [{"title": "Project", "value": "tenants"}]}]}}
    bot.mm._thread = [alert, {"id": "m2", "user_id": "U1", "create_at": 2, "message": "@berend why?"}]
    fake_llm.script = ["Pull secret.", "Renew it."]
    bot.on_event(posted("@berend why?", root_id="a1", pid="m2"))
    history = fake_llm.rounds[0][1]
    assert history["role"] == "assistant" and "ImagePullBackOff" in history["content"]
    assert "Project: tenants" in history["content"]
    bot.on_event(posted("and how do I fix it?", root_id="a1", mentions=(), pid="m3"))   # no mention needed
    assert len(bot.mm.posts) == 2


def test_mattermost_dm_and_reaction_feedback(bot, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    fake_llm.script = ["Yes."]
    bot.on_event(posted("is it up?", channel_type="D", mentions=()))
    assert bot.mm.posts[0]["root"] is None                     # DMs: inline reply
    bot.on_event({"event": "reaction_added", "data": {"reaction": json.dumps(
        {"user_id": "U1", "post_id": "p1", "emoji_name": "+1"})}})
    row = json.loads((tmp_path / "fb.jsonl").read_text())
    assert row["vote"] == "up" and row["question"] == "is it up?" and row["channel"] == "mattermost"


# --- health -------------------------------------------------------------------
def test_health_and_metrics_endpoints():
    server = health.serve(0)
    port = server.server_address[1]
    get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{port}{p}")   # noqa: E731
    assert get("/healthz").status == 200
    assert b"berend_questions_total" in get("/metrics").read()
    heartbeat("test-loop", -1)                                 # already stale
    with pytest.raises(urllib.error.HTTPError) as e:
        get("/healthz")
    assert e.value.code == 503
    from berend import metrics
    metrics._heartbeats.pop("test-loop")
    server.shutdown()
