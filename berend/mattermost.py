"""Mattermost REST client: bot token + channel id (threads, edits, chat) or incoming webhook."""
import logging

import requests

from . import config
from .metrics import ERRORS

log = logging.getLogger(__name__)

COLORS = {"critical": "#D32F2F", "warning": "#F9A825", "ok": "#2E7D32", "info": "#1E88E5"}


class Mattermost:
    def __init__(self):
        self.s = requests.Session()
        self.api = bool(config.MATTERMOST_URL and config.MATTERMOST_TOKEN)
        if self.api:
            self.s.headers["Authorization"] = f"Bearer {config.MATTERMOST_TOKEN}"

    def _url(self, path: str) -> str:
        return f"{config.MATTERMOST_URL}/api/v4/{path}"

    def post(self, text: str = "", attachments: list | None = None, root_id: str | None = None,
             channel_id: str | None = None) -> str | None:
        """Returns the post id in API mode (usable as root_id for replies), else None."""
        try:
            if self.api and (channel_id or config.MATTERMOST_CHANNEL_ID):
                body = {"channel_id": channel_id or config.MATTERMOST_CHANNEL_ID, "message": text,
                        "props": {"attachments": attachments or []}}
                if root_id:
                    body["root_id"] = root_id
                r = self.s.post(self._url("posts"), json=body, timeout=config.HTTP_TIMEOUT)
                r.raise_for_status()
                return r.json()["id"]
            if config.MATTERMOST_WEBHOOK_URL:
                body = {"text": text, "attachments": attachments or [], "username": config.MATTERMOST_USERNAME}
                if config.MATTERMOST_ICON_URL:
                    body["icon_url"] = config.MATTERMOST_ICON_URL
                r = self.s.post(config.MATTERMOST_WEBHOOK_URL, json=body, timeout=config.HTTP_TIMEOUT)
                r.raise_for_status()
        except Exception as e:
            ERRORS.labels("mattermost").inc()
            log.error("mattermost post failed: %s", e)
        return None

    def update(self, post_id: str | None, text: str = "", attachments: list | None = None) -> None:
        if not (self.api and post_id):
            return
        try:
            r = self.s.put(self._url(f"posts/{post_id}/patch"),
                           json={"message": text, "props": {"attachments": attachments or []}},
                           timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            ERRORS.labels("mattermost").inc()
            log.error("mattermost update failed: %s", e)

    def me(self) -> dict:
        r = self.s.get(self._url("users/me"), timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def thread(self, root_id: str) -> list[dict]:
        """All posts of a thread, oldest first."""
        r = self.s.get(self._url(f"posts/{root_id}/thread"), timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        posts = r.json().get("posts") or {}
        return sorted(posts.values(), key=lambda p: p.get("create_at", 0))
