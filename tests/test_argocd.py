import datetime
import time

import pytest

from berend import argocd, config


def mkapp(name, health="Healthy", sync="Synced", phase="Succeeded", resources=None, annotations=None):
    return {"metadata": {"name": name, "namespace": "openshift-gitops", "annotations": annotations or {}},
            "spec": {"project": "tenants",
                     "destination": {"server": "https://kubernetes.default.svc", "namespace": name}},
            "status": {"health": {"status": health}, "sync": {"status": sync, "revision": "3f9c2a1b77"},
                       "operationState": {"phase": phase, "message": "one or more objects failed to apply"},
                       "resources": resources or []}}


class FakeArgo:
    def __init__(self, apps):
        self.apps = apps

    def list_apps(self):
        return self.apps

    def resource_tree(self, name, app_ns):
        return {"nodes": [
            {"kind": "Deployment", "name": name, "namespace": name, "uid": "d1",
             "health": {"status": "Degraded", "message": "progress deadline exceeded"}},
            {"kind": "Pod", "name": f"{name}-7c9f", "namespace": name, "uid": "p1",
             "health": {"status": "Degraded", "message": "Back-off pulling image"}}]}

    def events(self, name, app_ns, node):
        if node["kind"] != "Pod":
            return []
        return [{"type": "Warning", "reason": "Failed", "count": 7, "lastTimestamp": "2026-10-29T20:00:00Z",
                 "message": 'Failed to pull image "registry.example.nl/payments:1.4": unauthorized'},
                {"type": "Normal", "reason": "Pulling", "message": "Pulling image"}]

    def logs(self, name, app_ns, pod, tail):
        return "ERROR cannot connect to db"


class FakeMM:
    api = True

    def __init__(self):
        self.posts, self.updates, self.n = [], [], 0

    def post(self, text="", attachments=None, root_id=None, channel_id=None):
        self.n += 1
        self.posts.append({"id": f"p{self.n}", "text": text, "att": attachments or [], "root": root_id})
        return f"p{self.n}"

    def update(self, post_id, text="", attachments=None):
        self.updates.append({"id": post_id, "att": attachments or []})


@pytest.fixture
def env():
    apps = [mkapp("payments-api"), mkapp("web")]
    argo, mm = FakeArgo(apps), FakeMM()
    mon = argocd.Monitor(argo=argo, mm=mm, analyze=False)
    mon.poll_once()                                           # startup summary
    return apps, argo, mm, mon


def titles(mm):
    return [a["title"] for p in mm.posts for a in p["att"]]


def test_evaluate_severities():
    assert argocd.evaluate(mkapp("a", health="Degraded")).severity == "critical"
    assert argocd.evaluate(mkapp("a", phase="Failed")).headline == "Sync failed"
    oos = argocd.evaluate(mkapp("a", sync="OutOfSync"))
    assert (oos.severity, oos.headline) == ("warning", "Out of sync")
    now = time.time()
    assert argocd.evaluate(mkapp("a", health="Progressing"), now - 3600, now).headline == "Stuck progressing"
    assert argocd.evaluate(mkapp("a", health="Progressing"), now - 60, now).severity == "ok"


def test_startup_summary_then_debounced_alert_with_events_and_resolve(env):
    apps, argo, mm, mon = env
    assert titles(mm) == ["👀 Watching 2 Argo CD apps — all healthy ✅"]

    apps[0] = mkapp("payments-api", "Degraded", "OutOfSync", "Failed")
    mon.poll_once()
    assert len(mm.posts) == 1                                 # debounced: not yet
    mon.poll_once()
    alert = mm.posts[-1]["att"][0]
    assert alert["title"] == "🔴 payments-api — Degraded, Sync failed, Out of sync"
    assert "unauthorized" in alert["text"] and "**Events**" in alert["text"]
    assert "`Pod/payments-api-7c9f`" in alert["text"]          # pod found via resource tree
    assert alert["title_link"] == "https://argo.test/applications/openshift-gitops/payments-api"

    apps[0] = mkapp("payments-api")
    mon.poll_once()
    mon.poll_once()
    assert mm.updates[-1]["att"][0]["title"] == "🟢 payments-api — Resolved"
    assert mm.posts[-1]["root"] == "p2" and "healthy again" in mm.posts[-1]["text"]


def test_flapping_is_not_alerted(env):
    apps, argo, mm, mon = env
    for health in ("Degraded", "Healthy", "Degraded", "Healthy"):
        apps[0] = mkapp("payments-api", health)
        mon.poll_once()
    assert len(mm.posts) == 1


def test_restart_does_not_realert(env):
    apps, argo, mm, mon = env
    apps[0] = mkapp("payments-api", "Degraded")
    mon.poll_once()
    mon.poll_once()
    n = len(mm.posts)
    mon2 = argocd.Monitor(argo=argo, mm=mm, analyze=False)
    mon2.poll_once()
    mon2.poll_once()
    assert len(mm.posts) == n


def test_storm_is_grouped_and_closed_when_all_recover():
    apps = [mkapp(f"app{i}") for i in range(8)]
    argo, mm = FakeArgo(apps), FakeMM()
    mon = argocd.Monitor(argo=argo, mm=mm, analyze=False)
    mon.poll_once()
    for i in range(6):
        apps[i] = mkapp(f"app{i}", "Degraded")
    mon.poll_once()
    mon.poll_once()
    storm = mm.posts[-1]
    assert storm["att"][0]["title"] == "🔴 6 Argo CD apps became unhealthy at once"
    assert len(mm.posts) == 2                                  # summary + 1 grouped alert, no flood

    for i in range(6):
        apps[i] = mkapp(f"app{i}")
    mon.poll_once()
    mon.poll_once()
    replies = [p for p in mm.posts if p["root"] == storm["id"]]
    assert len(replies) == 6 and "5 of 6 still unhealthy" in replies[0]["text"]
    assert mm.updates[-1]["att"][0]["title"].startswith("🟢 Resolved — all 6 apps healthy again")
    assert mon.state["groups"] == {}


def test_mute_by_annotation_and_regex(env, monkeypatch):
    apps, argo, mm, mon = env
    future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
    assert argocd.muted(mkapp("a", annotations={"berend.io/mute": "true"}))
    assert argocd.muted(mkapp("a", annotations={"berend.io/mute": future}))
    assert not argocd.muted(mkapp("a", annotations={"berend.io/mute": past}))
    monkeypatch.setattr(config, "ARGOCD_IGNORE_APPS", ["sandbox-.*"])
    assert argocd.muted(mkapp("sandbox-jan")) and not argocd.muted(mkapp("prod-sandbox"))

    apps[0] = mkapp("payments-api", "Degraded", annotations={"berend.io/mute": "true"})
    mon.poll_once()
    mon.poll_once()
    assert len(mm.posts) == 1


def test_reminder_in_thread(env, monkeypatch):
    apps, argo, mm, mon = env
    monkeypatch.setattr(config, "ALERT_REMIND_HOURS", 1)
    apps[0] = mkapp("payments-api", "Degraded")
    mon.poll_once()
    mon.poll_once()
    mon.state["apps"]["openshift-gitops/payments-api"]["since"] -= 2 * 3600
    mon.poll_once()
    assert mm.posts[-1]["text"].startswith("⏰ **payments-api** is still unhealthy")
    assert mm.posts[-1]["root"] == "p2"
    mon.poll_once()
    assert not mm.posts[-1]["text"].startswith("⏰") or len([p for p in mm.posts if "⏰" in p["text"]]) == 1


def test_analysis_prompt_contains_evidence(env, fake_llm, monkeypatch):
    apps, argo, mm, mon = env
    monkeypatch.setattr(config, "ARGOCD_FETCH_LOGS", True)
    s = argocd.diagnose(argocd.evaluate(mkapp("payments-api", "Degraded")), argo)
    fake_llm.script = ["- The image pull fails with `unauthorized`: renew `registry-creds`."]
    mon._analyze(s, "p9")
    prompt = fake_llm.rounds[0][-1]["content"]
    assert "unauthorized" in prompt and "cannot connect to db" in prompt
    assert mm.posts[-1]["root"] == "p9" and "possible cause" in mm.posts[-1]["text"]


def test_describe_named_app_digs_deeper(monkeypatch):
    argocd._client = FakeArgo([mkapp("payments-api", "Degraded"), mkapp("web")])
    text, sources = argocd.describe_apps("payments")
    assert "unauthorized" in text and sources == [("Argo CD: payments-api",
                                                   "https://argo.test/applications/openshift-gitops/payments-api")]
    text, _ = argocd.describe_apps("unhealthy")
    assert "payments-api" in text and "web" not in text
