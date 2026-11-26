"""Slack front end: mrkdwn conversion, block building, and the event handlers
against a fake Slack client (no network, no Socket Mode)."""
import json

import pytest

from berend import agent, chat, config, slack_bot


class FakeSlack:
    def __init__(self, replies=()):
        self.calls, self.replies = [], list(replies)

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
            if name == "chat_postMessage":
                return {"ts": "999.1"}
            if name == "conversations_replies":
                return {"messages": self.replies}
            return {}
        return call

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
def answer(monkeypatch):
    seen = {}

    def fake_ask(question, history, channel, set_text, render=lambda t: t):
        seen.update(question=question, history=history, channel=channel)
        set_text("partial")
        return agent.Answer(text="**Yes**, see [docs](https://d.test/x)", sources=[("Doc | <1>", "https://d.test/x")],
                            tools_used=["search_knowledge_base", "search_knowledge_base", "web_search"], seconds=1.25)

    monkeypatch.setattr(chat, "ask", fake_ask)
    monkeypatch.setattr(slack_bot, "BOT_USER_ID", "UBOT")
    slack_bot._threads.clear()
    return seen


def test_to_mrkdwn_converts_outside_code_only():
    md = "# Title\n**bold** and __b2__\n- item\n  * sub\n[x](https://a.b)\n```\n**not bold** [x](https://a.b)\n```"
    out = slack_bot.to_mrkdwn(md)
    assert out.startswith("*Title*\n*bold* and *b2*\n• item\n  • sub\n<https://a.b|x>\n")
    assert out.endswith("```\n**not bold** [x](https://a.b)\n```")


def test_split_keeps_code_fences_balanced():
    text = "intro\n```\n" + "\n".join(f"line {i}" for i in range(400)) + "\n```\nend"
    parts = slack_bot._split(text, limit=500)
    assert len(parts) > 3 and all(len(p) <= 520 for p in parts)
    assert all(p.count("```") % 2 == 0 for p in parts)
    assert slack_bot._split("") == [" "]


def test_build_blocks(answer):
    ans = agent.Answer(text="hello", sources=[("A|<b>", "https://x")], tools_used=["web_search"], seconds=2)
    blocks = slack_bot.build_blocks(ans, "q" * 3000)
    assert blocks[0] == {"type": "section", "text": {"type": "mrkdwn", "text": "hello\n"}}
    assert "<https://x|A  b >" in json.dumps(blocks[1])                 # link text sanitised
    assert "🌐 web" in blocks[2]["elements"][0]["text"] and config.CHAT_MODEL in blocks[2]["elements"][0]["text"]
    buttons = blocks[3]["elements"]
    assert [b["action_id"] for b in buttons] == ["feedback_up", "feedback_down"]
    assert len(buttons[0]["value"]) == 1900


def test_clean_strips_mentions():
    assert slack_bot.clean("<@U123ABC> what is up <@UBOT>") == "what is up"
    assert slack_bot.clean(None) == ""


def test_mention_answers_in_a_thread_with_blocks(answer):
    slack = FakeSlack()
    slack_bot.handle({"channel": "C1", "ts": "1.0", "text": "<@UBOT> is payments ok?"}, slack)
    assert answer == {"question": "is payments ok?", "history": [], "channel": "slack"}
    names = slack.names()
    assert names[0] == "reactions_add" and names[1] == "chat_postMessage" and names[-1] == "reactions_remove"
    post = dict(slack.calls[1][1])
    assert post["thread_ts"] == "1.0" and "thinking" in post["text"]
    final = [kw for n, kw in slack.calls if n == "chat_update" and "blocks" in kw][0]
    assert final["text"].startswith("**Yes**") and final["blocks"][0]["text"]["text"].startswith("*Yes*")
    assert ("C1", "1.0") in slack_bot._threads


def test_thread_history_roles_and_filtering(answer):
    replies = [
        {"ts": "1.0", "user": "U1", "text": "<@UBOT> first question"},
        {"ts": "1.1", "user": "UBOT", "text": "first answer"},
        {"ts": "1.2", "bot_id": "B1", "text": "_🤔 Berend is thinking…_"},
        {"ts": "1.3", "user": "U1", "text": ""},
        {"ts": "1.4", "user": "U1", "text": "follow-up"},
    ]
    slack_bot.handle({"channel": "C1", "ts": "1.4", "thread_ts": "1.0", "text": "follow-up"}, FakeSlack(replies))
    assert answer["history"] == [{"role": "user", "content": "first question"},
                                 {"role": "assistant", "content": "first answer"}]


def test_allowed_channels_and_empty_questions(answer, monkeypatch):
    monkeypatch.setattr(config, "SLACK_ALLOWED_CHANNELS", ["C-OK"])
    slack = FakeSlack()
    slack_bot.handle({"channel": "C-OTHER", "ts": "1", "text": "hi"}, slack)
    assert slack.calls == []
    slack_bot.handle({"channel": "D1", "ts": "1", "text": "hi", "channel_type": "im"}, slack)   # DMs always
    assert "chat_postMessage" in slack.names()
    slack2 = FakeSlack()
    slack_bot.handle({"channel": "C-OK", "ts": "1", "text": "<@UBOT>"}, slack2)
    assert slack2.calls == []


def test_agent_errors_are_shown_in_the_placeholder(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(chat, "ask", broken)
    slack = FakeSlack()
    slack_bot.handle({"channel": "C1", "ts": "1", "text": "hi"}, slack)
    texts = [kw.get("text") for n, kw in slack.calls if n == "chat_update"]
    assert texts == [":warning: Something went wrong: `ollama down`"]
    assert slack.names()[-1] == "reactions_remove"


def test_message_routing(answer, monkeypatch):
    handled = []
    monkeypatch.setattr(slack_bot, "handle", lambda e, c: handled.append(e["ts"]))
    on = slack_bot.on_message
    on({"ts": "a", "channel": "D", "channel_type": "im", "text": "dm"}, None)
    on({"ts": "b", "channel": "C", "subtype": "message_changed"}, None)
    on({"ts": "c", "channel": "C", "bot_id": "B"}, None)
    on({"ts": "d", "channel": "C", "thread_ts": "t1", "text": "follow"}, None)        # not our thread
    slack_bot._threads.put(("C", "t1"))
    on({"ts": "e", "channel": "C", "thread_ts": "t1", "text": "follow"}, None)        # our thread
    on({"ts": "f", "channel": "C", "thread_ts": "t1", "text": "<@UBOT> again"}, None)  # comes via app_mention
    monkeypatch.setattr(config, "SLACK_THREAD_FOLLOWUPS", False)
    on({"ts": "g", "channel": "C", "thread_ts": "t1", "text": "follow"}, None)
    assert handled == ["a", "e"]


def test_feedback_button_logs_and_replaces_buttons(answer, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_LOG", str(tmp_path / "fb" / "feedback.jsonl"))
    acked, slack = [], FakeSlack()
    body = {"user": {"id": "U7"}, "channel": {"id": "C1"},
            "message": {"ts": "5.0", "text": "answer text",
                        "blocks": [{"type": "section"}, {"type": "actions", "block_id": "feedback"}]}}
    slack_bot.on_feedback(lambda: acked.append(1), body, slack, {"action_id": "feedback_down", "value": "the q"})
    assert acked == [1]
    entry = json.loads((tmp_path / "fb" / "feedback.jsonl").read_text())
    assert (entry["vote"], entry["user"], entry["question"], entry["answer"]) == ("down", "U7", "the q", "answer text")
    blocks = slack.calls[0][1]["blocks"]
    assert [b.get("block_id") for b in blocks] == [None, None]
    assert "👎 Thanks for the feedback, <@U7>" in json.dumps(blocks, ensure_ascii=False)
