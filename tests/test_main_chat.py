"""main(): what starts for which configuration; chat helpers."""
import threading

import pytest

from berend import agent, chat, config, health, main


class Stop(Exception):
    pass


started_events = {}


@pytest.fixture
def started(monkeypatch):
    log = []
    monkeypatch.setattr(health, "serve", lambda port: log.append(("health", port)))
    monkeypatch.setattr(agent, "warmup", lambda: None)
    argo = threading.Event()
    monkeypatch.setattr("berend.argocd.run_forever", lambda: (log.append(("argocd",)), argo.set()))
    started_events["argo"] = argo
    monkeypatch.setattr("berend.mattermost_bot.start", lambda: log.append(("mattermost",)))
    monkeypatch.setattr("berend.slack_bot.start", lambda block: log.append(("slack", block)))

    def no_forever(seconds):
        raise Stop()

    monkeypatch.setattr(main.time, "sleep", no_forever)
    health.ready.clear()
    return log


def test_nothing_configured_exits(monkeypatch, started):
    for k in ("SLACK_ENABLED", "MONITOR_ENABLED", "MATTERMOST_CHAT"):
        monkeypatch.setattr(config, k, False)
    with pytest.raises(SystemExit, match="Nothing to run"):
        main.main()


def test_mattermost_and_argocd(monkeypatch, started):
    monkeypatch.setattr(config, "SLACK_ENABLED", False)
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    monkeypatch.setattr(config, "MATTERMOST_CHAT", True)
    monkeypatch.setattr(config, "HEALTH_PORT", 0)
    monkeypatch.setattr(config, "REINDEX_INTERVAL_HOURS", 0)
    with pytest.raises(Stop):                       # reached the idle loop
        main.main()
    assert started_events["argo"].wait(5)            # argocd runs on its own thread
    assert ("mattermost",) in started and ("argocd",) in started
    assert not any(e[0] == "health" for e in started)
    assert health.ready.is_set()


def test_slack_blocks_and_health_port(monkeypatch, started):
    monkeypatch.setattr(config, "SLACK_ENABLED", True)
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    monkeypatch.setattr(config, "MATTERMOST_CHAT", False)
    monkeypatch.setattr(config, "HEALTH_PORT", 18080)
    main.main()
    assert started[0] == ("health", 18080) and started[-1] == ("slack", True)


def test_reindex_loop_survives_a_failed_run(monkeypatch):
    runs, sleeps = [], []
    monkeypatch.setattr(config, "REINDEX_INTERVAL_HOURS", 2)

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) > 2:
            raise Stop()

    def run():
        runs.append(1)
        raise RuntimeError("source down")

    monkeypatch.setattr(main.time, "sleep", sleep)
    monkeypatch.setattr(main.ingest, "run", run)
    with pytest.raises(Stop):
        main._reindex_loop()
    assert sleeps[:2] == [7200, 7200] and len(runs) == 2


def test_tools_summary_dedupes_in_order():
    ans = agent.Answer(text="", tools_used=["web_search", "search_knowledge_base", "web_search", "custom"])
    assert chat.tools_summary(ans) == "🌐 web  📚 docs  🔧 custom"


def test_ask_shows_tool_status_and_throttles_streaming(monkeypatch):
    shown = []

    def fake_answer(q, history, on_tool, on_text):
        on_tool("fetch_url", {"url": "https://x.test/page"})
        on_text("partial")
        on_tool("argocd_apps", None)
        return agent.Answer(text="done", seconds=0.5)

    monkeypatch.setattr(agent, "answer", fake_answer)
    monkeypatch.setattr(config, "STREAM_UPDATE_SECONDS", 3600)
    ans = chat.ask("q", [], "mattermost", shown.append, render=str.upper)
    assert ans.text == "done"
    assert shown == ["_📄 searching page… https://x.test/page_", "_🐙 searching Argo CD… _"]   # stream throttled
    monkeypatch.setattr(config, "STREAM_UPDATE_SECONDS", 0)
    shown.clear()
    chat.ask("q", [], "slack", shown.append, render=str.upper)
    assert "PARTIAL ▍" in shown


def test_ask_counts_errors(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(agent, "answer", broken)
    before = chat.ERRORS.labels("chat_slack")._value.get()
    with pytest.raises(RuntimeError):
        chat.ask("q", [], "slack", lambda t: None)
    assert chat.ERRORS.labels("chat_slack")._value.get() == before + 1


def test_feedback_log_failure_is_swallowed(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(config, "FEEDBACK_LOG", str(blocker / "sub" / "fb.jsonl"))
    chat.log_feedback("slack", "u", "up", "q")
