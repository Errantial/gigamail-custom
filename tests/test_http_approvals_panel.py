"""The console's "waiting for you" panel: pending requests say who sends
them once approved, and recently decided ones say what happened.

Before this the new console showed only the requests its own agent bar
raised: a draft the watcher prepared from a rule was visible on Telegram
and in `gigamail approvals list`, and nowhere on screen.
"""
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from gigamail import policy
from gigamail.core import rules as rules_mod


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    policy.set_store(policy.ApprovalStore(tmp_path / "approvals.db"))
    yield
    policy.set_store(None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADE_CONSOLE_TOKEN", "tok")
    from gigamail import http_api
    importlib.reload(http_api)
    with TestClient(http_api.app, headers={"X-ADE-Token": "tok"}) as c:
        yield c
    monkeypatch.delenv("ADE_CONSOLE_TOKEN")
    importlib.reload(http_api)


def _request(to="mario.rossi@example.com", ttl=900.0):
    preview = {"to": to, "subject": "Visit", "body": "hello"}
    return policy.store().create("send_mail", {"to": to}, preview, ttl=ttl)


def _born_from_rule(rid):
    rules_mod.store().record("rule_x", 1, "m1", "mario.rossi@example.com",
                             "awaiting_approval", request_id=rid)


def test_pending_requests_say_who_completes_them(client):
    agent_rid = _request()
    watcher_rid = _request(to="anna.bianchi@example.com")
    _born_from_rule(watcher_rid)
    by_id = {r["request_id"]: r for r in client.get("/approvals").json()}
    assert by_id[agent_rid]["completed_by"] == "agent"
    assert by_id[watcher_rid]["completed_by"] == "watcher"
    assert by_id[agent_rid]["preview"]["subject"] == "Visit"


def test_recent_lists_what_was_decided_and_how_it_went(client):
    rejected = _request()
    policy.store().reject(rejected, by="console:tester")
    sent = _request(to="anna.bianchi@example.com")
    policy.store().approve(sent, by="console:tester")
    assert policy.store().consume_approved(sent, "send_mail")
    policy.store().record_outcome(sent, "ok", {"accepted": 1})
    still_waiting = _request(to="luca.verdi@example.com")
    expired = _request(to="old@example.com", ttl=0.001)
    time.sleep(0.01)

    recent = {r["request_id"]: r for r in client.get("/approvals/recent").json()}
    assert set(recent) == {rejected, sent, expired}
    assert recent[rejected]["status"] == "rejected"
    assert recent[rejected]["decided_by"] == "console:tester"
    assert recent[sent]["status"] == "executed"
    assert recent[sent]["execution_outcome"] == "ok"
    assert recent[sent]["provider_result"] == {"accepted": 1}
    assert recent[expired]["status"] == "pending" and recent[expired]["expired"] is True
    # the one still waiting is in the pending list, not here
    assert [r["request_id"] for r in client.get("/approvals").json()] == [still_waiting]


def test_recent_window_is_bounded(client):
    rid = _request()
    policy.store().reject(rid, by="console:tester")
    assert client.get("/approvals/recent?hours=0").json() == []
    assert client.get("/approvals/recent?hours=-5").json() == []
    assert [r["request_id"] for r in client.get("/approvals/recent?hours=99999").json()] == [rid]


def test_approving_reports_who_sends(client, monkeypatch):
    monkeypatch.setenv("GIGAMAIL_CONSENT_BACKEND", "allow")
    monkeypatch.setenv("ADE_MAIL_DRYRUN", "1")
    agent_rid = _request()
    watcher_rid = _request(to="anna.bianchi@example.com")
    _born_from_rule(watcher_rid)
    r = client.post(f"/approvals/{agent_rid}/approve")
    assert r.status_code == 200 and r.json()["completed_by"] == "agent"
    r = client.post(f"/approvals/{watcher_rid}/approve")
    assert r.status_code == 200 and r.json()["completed_by"] == "watcher"
    assert policy.store().get(watcher_rid)["status"] == policy.APPROVED
