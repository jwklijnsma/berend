"""Slack front end (Socket Mode: no public ingress needed).

Answers @mentions in channels and all DMs, in a thread, using the thread as context.
Follow-ups in a thread Berend already answered need no new @mention.
The reply streams in live, then is rendered as Block Kit: answer, sources, a footer with
timing and tools, and 👍/👎 feedback buttons (logged to FEEDBACK_LOG).
"""
import logging
import re

from slack_bolt import App

from . import agent, chat, config
from .mattermost_bot import _Bounded

log = logging.getLogger(__name__)
app = App(token=config.SLACK_BOT_TOKEN or "xoxb-unset", token_verification_enabled=False)
BOT_USER_ID = None
_threads = _Bounded(2000)            # (channel, thread_ts) Berend has answered in


# --- formatting -----------------------------------------------------------
def to_mrkdwn(text: str) -> str:
    """Markdown from the model -> Slack mrkdwn (outside code blocks)."""
    parts = re.split(r"(```.*?```)", text, flags=re.S)
    for i in range(0, len(parts), 2):
        p = parts[i]
        p = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", p)     # links
        p = re.sub(r"\*\*(.+?)\*\*", r"*\1*", p)                            # bold
        p = re.sub(r"__(.+?)__", r"*\1*", p)
        p = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", p, flags=re.M)              # headings
        p = re.sub(r"^(\s*)[-*]\s+", r"\1• ", p, flags=re.M)                # bullets
        parts[i] = p
    return "".join(parts)


def _split(text: str, limit: int = 2900) -> list[str]:
    """Split for section blocks (3000 char max) without breaking code fences."""
    out, cur, in_code = [], "", False
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur + ("\n```" if in_code else ""))
            cur = "```\n" if in_code else ""
        cur += line + "\n"
        if line.strip().startswith("```"):
            in_code = not in_code
    if cur.strip():
        out.append(cur)
    return out or [" "]


def _link_text(t: str) -> str:
    return re.sub(r"[|<>]", " ", t)[:70]


def build_blocks(ans: agent.Answer, question: str) -> list[dict]:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": part}}
              for part in _split(to_mrkdwn(ans.text))][:45]
    if ans.sources:
        links = "   ".join(f"<{u}|{_link_text(t)}>" for t, u in ans.sources[:6])
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"📎 {links}"}]})
    tools = chat.tools_summary(ans)
    footer = (f"*{config.AGENT_NAME}*  ·  ⏱ {ans.seconds:.1f}s" + (f"  ·  {tools}" if tools else "")
              + f"  ·  🦙 {config.CHAT_MODEL}")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    blocks.append({"type": "actions", "block_id": "feedback", "elements": [
        {"type": "button", "action_id": "feedback_up", "text": {"type": "plain_text", "text": "👍 Helpful"},
         "value": question[:1900]},
        {"type": "button", "action_id": "feedback_down", "text": {"type": "plain_text", "text": "👎 Not helpful"},
         "value": question[:1900]},
    ]})
    return blocks


def clean(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def thread_history(client, channel: str, thread_ts: str, current_ts: str) -> list[dict]:
    try:
        msgs = client.conversations_replies(channel=channel, ts=thread_ts, limit=50)["messages"]
    except Exception:
        return []
    hist = []
    for m in msgs:
        text = m.get("text") or ""
        if m.get("ts") == current_ts or not text or "is thinking…" in text:
            continue
        role = "assistant" if m.get("user") == BOT_USER_ID or m.get("bot_id") else "user"
        hist.append({"role": role, "content": clean(text)[:3000]})
    return hist


# --- handlers -------------------------------------------------------------
def handle(event, client):
    channel, ts = event["channel"], event["ts"]
    if config.SLACK_ALLOWED_CHANNELS and channel not in config.SLACK_ALLOWED_CHANNELS \
            and event.get("channel_type") != "im":
        return
    question = clean(event.get("text", ""))
    if not question:
        return
    thread_ts = event.get("thread_ts") or ts
    history = thread_history(client, channel, thread_ts, ts) if event.get("thread_ts") else []

    try:
        client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass
    placeholder = client.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                          text=f"_🤔 {config.AGENT_NAME} is thinking…_")

    def update(text):
        try:
            client.chat_update(channel=channel, ts=placeholder["ts"], text=text)
        except Exception as e:
            log.debug("update failed: %s", e)

    try:
        ans = chat.ask(question, history, "slack", update, render=to_mrkdwn)
        client.chat_update(channel=channel, ts=placeholder["ts"], text=ans.text[:3000],
                           blocks=build_blocks(ans, question))
        _threads.put((channel, thread_ts))
    except Exception as e:
        log.exception("agent failed")
        update(f":warning: Something went wrong: `{e}`")
    try:
        client.reactions_remove(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass


@app.event("app_mention")
def on_mention(event, client):
    handle(event, client)


@app.event("message")
def on_message(event, client):
    if event.get("subtype") or event.get("bot_id"):
        return
    if event.get("channel_type") == "im":
        handle(event, client)
    elif (config.SLACK_THREAD_FOLLOWUPS and event.get("thread_ts")
          and (event["channel"], event["thread_ts"]) in _threads
          and f"<@{BOT_USER_ID}>" not in (event.get("text") or "")):   # mentions come via app_mention
        handle(event, client)


@app.action(re.compile(r"feedback_(up|down)"))
def on_feedback(ack, body, client, action):
    ack()
    vote = "up" if action["action_id"].endswith("up") else "down"
    msg = body["message"]
    chat.log_feedback("slack", body["user"]["id"], vote, action.get("value", ""), msg.get("text", ""))
    blocks = [b for b in msg.get("blocks", []) if b.get("block_id") != "feedback"]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   f"{'👍' if vote == 'up' else '👎'} Thanks for the feedback, <@{body['user']['id']}>"}]})
    client.chat_update(channel=body["channel"]["id"], ts=msg["ts"], text=msg.get("text", ""), blocks=blocks)


def start(block: bool = True):
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    global BOT_USER_ID
    BOT_USER_ID = app.client.auth_test()["user_id"]
    handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
    log.info("slack bot %s connected", BOT_USER_ID)
    handler.start() if block else handler.connect()


if __name__ == "__main__":
    from .main import main
    main()
