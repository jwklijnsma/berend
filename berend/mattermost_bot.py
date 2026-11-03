"""Chat with Berend in Mattermost over the websocket API (outbound only, no ingress needed).

Answers DMs and @mentions; follow-ups in a thread Berend already answered need no mention.
In an alert thread the alert itself is part of the context, so "@berend why?" or
"@berend what changed in this revision?" work directly under an alert.
React 👍 / 👎 on an answer to leave feedback.
"""
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import websocket

from . import chat, config
from .mattermost import Mattermost

log = logging.getLogger(__name__)
UP, DOWN = {"+1", "thumbsup", "white_check_mark"}, {"-1", "thumbsdown"}


class _Bounded(OrderedDict):
    def __init__(self, size=1000):
        super().__init__()
        self.size = size

    def put(self, key, value=True):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)


def flatten(post: dict) -> str:
    """Message text plus attachment content (alerts are attachments)."""
    parts = [post.get("message") or ""]
    for a in (post.get("props") or {}).get("attachments") or []:
        parts += [a.get("title") or "", a.get("text") or ""]
        parts += [f"{f.get('title')}: {f.get('value')}" for f in a.get("fields") or []]
    return "\n".join(p for p in parts if p).strip()


class MattermostBot:
    def __init__(self, mm: Mattermost | None = None):
        self.mm = mm or Mattermost()
        me = self.mm.me()
        self.me, self.username = me["id"], me.get("username", "berend")
        self.pool = ThreadPoolExecutor(4)
        self.answers = _Bounded()          # answer post id -> question (for reaction feedback)
        self.threads = _Bounded()          # thread roots Berend has replied in

    # --- event handling ---------------------------------------------------
    def on_event(self, ev: dict):
        kind, data = ev.get("event"), ev.get("data") or {}
        if kind == "posted":
            self.on_posted(data)
        elif kind == "reaction_added":
            self.on_reaction(data)

    def on_posted(self, data: dict):
        post = json.loads(data.get("post") or "{}")
        props = post.get("props") or {}
        if (not post or post.get("user_id") == self.me or post.get("type")
                or props.get("from_bot") == "true" or props.get("from_webhook") == "true"):
            return
        is_dm = data.get("channel_type") == "D"
        mentioned = self.me in json.loads(data.get("mentions") or "[]") or \
            f"@{self.username}" in (post.get("message") or "").lower()
        followup = config.MATTERMOST_THREAD_FOLLOWUPS and post.get("root_id") in self.threads
        if not (is_dm or mentioned or followup):
            return
        if config.MATTERMOST_CHAT_CHANNELS and not is_dm and post["channel_id"] not in config.MATTERMOST_CHAT_CHANNELS:
            return
        self.pool.submit(self._safe_handle, post, is_dm)

    def on_reaction(self, data: dict):
        r = json.loads(data.get("reaction") or "{}")
        question = self.answers.get(r.get("post_id"))
        if not question or r.get("user_id") == self.me:
            return
        emoji = r.get("emoji_name", "")
        vote = "up" if emoji in UP else "down" if emoji in DOWN else None
        if vote:
            chat.log_feedback("mattermost", r.get("user_id", ""), vote, question)

    # --- answering --------------------------------------------------------
    def history(self, root_id: str, current_id: str) -> list[dict]:
        try:
            posts = self.mm.thread(root_id)
        except Exception as e:
            log.warning("thread fetch failed: %s", e)
            return []
        out = []
        for p in posts:
            text = flatten(p)
            if p.get("id") == current_id or not text or text.startswith("_🤔"):
                continue
            role = "assistant" if p.get("user_id") == self.me else "user"
            out.append({"role": role, "content": self.strip(text)[:3000]})
        return out

    def strip(self, text: str) -> str:
        return re.sub(rf"@{re.escape(self.username)}\b", "", text, flags=re.I).strip()

    def _safe_handle(self, post: dict, is_dm: bool):
        try:
            self.handle(post, is_dm)
        except Exception:
            log.exception("mattermost answer failed")

    def handle(self, post: dict, is_dm: bool):
        question = self.strip(post.get("message") or "")
        if not question:
            return
        root = post.get("root_id") or ("" if is_dm else post["id"])   # DMs: reply inline unless threaded
        history = self.history(post["root_id"], post["id"]) if post.get("root_id") else []
        pid = self.mm.post(f"_🤔 {config.AGENT_NAME} is thinking…_", root_id=root or None,
                           channel_id=post["channel_id"])
        try:
            ans = chat.ask(question, history, "mattermost", lambda t: self.mm.update(pid, t))
        except Exception as e:
            self.mm.update(pid, f":warning: Something went wrong: `{e}`")
            return
        self.mm.update(pid, render(ans))
        if pid:
            self.answers.put(pid, question)
        self.threads.put(root or pid)


def render(ans) -> str:
    out = ans.text
    if ans.sources:
        out += "\n\n📎 " + " · ".join(f"[{t[:70]}]({u})" for t, u in ans.sources[:6])
    tools = chat.tools_summary(ans)
    out += (f"\n\n_{config.AGENT_NAME} · ⏱ {ans.seconds:.1f}s"
            + (f" · {tools}" if tools else "") + f" · 🦙 {config.CHAT_MODEL} · react 👍/👎_")
    return out


def run_forever():
    while True:                                   # Mattermost may be down when we start
        try:
            bot = MattermostBot()
            break
        except Exception as e:
            log.warning("mattermost chat: login failed (%s), retrying in 30s", e)
            time.sleep(30)
    url = re.sub(r"^http", "ws", config.MATTERMOST_URL) + "/api/v4/websocket"
    ca = os.getenv("REQUESTS_CA_BUNDLE")
    sslopt = {"ca_certs": ca} if ca else {}
    log.info("mattermost chat: listening as @%s", bot.username)
    backoff = 2

    def on_open(ws):
        ws.send(json.dumps({"seq": 1, "action": "authentication_challenge",
                            "data": {"token": config.MATTERMOST_TOKEN}}))

    def on_message(ws, raw):
        try:
            bot.on_event(json.loads(raw))
        except Exception:
            log.exception("bad websocket event")

    while True:
        started = time.time()
        ws = websocket.WebSocketApp(url, header=[f"Authorization: Bearer {config.MATTERMOST_TOKEN}"],
                                    on_open=on_open, on_message=on_message,
                                    on_error=lambda ws, e: log.warning("mattermost websocket: %s", e))
        ws.run_forever(ping_interval=30, ping_timeout=10, sslopt=sslopt)
        backoff = 2 if time.time() - started > 60 else min(backoff * 2, 120)
        log.info("mattermost websocket closed, reconnecting in %ss", backoff)
        time.sleep(backoff)


def start():
    threading.Thread(target=run_forever, daemon=True, name="mattermost-chat").start()
