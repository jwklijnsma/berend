"""Berend entry point: starts whatever is configured.

- Slack Q&A bot            (SLACK_BOT_TOKEN + SLACK_APP_TOKEN)
- Mattermost Q&A bot       (MATTERMOST_URL + MATTERMOST_TOKEN, MATTERMOST_CHAT=true)
- Argo CD -> Mattermost    (ARGOCD_URL + ARGOCD_TOKEN + Mattermost settings)
- background re-index      (REINDEX_INTERVAL_HOURS > 0)
- /healthz /readyz /metrics on HEALTH_PORT
"""
import logging
import threading
import time

from . import agent, config, health, ingest, store
from .metrics import INDEX_CHUNKS, heartbeat

log = logging.getLogger("berend")


def _reindex_loop():
    while True:
        heartbeat("reindex", config.REINDEX_INTERVAL_HOURS * 3600 * 3 + 3600)
        time.sleep(config.REINDEX_INTERVAL_HOURS * 3600)
        try:
            ingest.run()
        except Exception:
            log.exception("scheduled re-index failed")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "websocket", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    chat_mm = config.MATTERMOST_CHAT and config.MATTERMOST_URL and config.MATTERMOST_TOKEN
    if not (config.SLACK_ENABLED or config.MONITOR_ENABLED or chat_mm):
        raise SystemExit("Nothing to run: configure Slack tokens, MATTERMOST_* and/or ARGOCD_*.")

    if config.HEALTH_PORT:
        health.serve(config.HEALTH_PORT)
    try:
        INDEX_CHUNKS.set(store.count())
    except Exception as e:
        log.warning("index not readable yet: %s", e)
    threading.Thread(target=agent.warmup, daemon=True).start()
    if config.REINDEX_INTERVAL_HOURS > 0:
        threading.Thread(target=_reindex_loop, daemon=True, name="reindex").start()

    if config.MONITOR_ENABLED:
        from . import argocd
        threading.Thread(target=argocd.run_forever, daemon=True, name="argocd").start()
    elif config.ARGOCD_ENABLED:
        log.info("Argo CD configured without Mattermost: available as a chat tool only")

    if chat_mm:
        from . import mattermost_bot
        mattermost_bot.start()

    health.ready.set()
    if config.SLACK_ENABLED:
        from . import slack_bot
        slack_bot.start(block=True)
    else:
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
