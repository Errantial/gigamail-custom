"""Operator-configured immediate mail never changes existing approval requests."""
import pytest

from gigamail import policy, server


@pytest.fixture
def mail_context(tmp_path, monkeypatch):
    policy.set_store(policy.ApprovalStore(tmp_path / "approvals.db"))
    account = {"id": 1, "email": "sender@example.com"}
    monkeypatch.setattr(server.core_accounts, "get_active_account", lambda: account)
    monkeypatch.setattr(server.core_accounts, "get_account_by_id", lambda aid: account)
    monkeypatch.setattr(server, "_reply_already_pending", lambda mid, **kw: None)
    monkeypatch.setattr(server.signature, "apply", lambda aid, body: body + " signed")
    monkeypatch.setattr(policy, "notify_approval_requested", lambda *a, **kw: None)
    monkeypatch.delenv("ADE_MAIL_DRYRUN", raising=False)
    monkeypatch.delenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", raising=False)
    calls = []
    for backend in ("send_message", "reply_message"):
        monkeypatch.setattr(server.mail_router, backend,
                            lambda **kw: calls.append(kw) or {"success": True})
    monkeypatch.setattr(server.mail_router, "get_message", lambda **kw: {})
    yield calls
    policy.set_store(None)


@pytest.mark.parametrize("tool,kwargs", [
    ("send_mail", {"to": "recipient@example.com", "subject": "Test", "body": "Hello"}),
    ("reply_mail", {"message_id": "42", "body": "Hello"}),
])
def test_immediate_mail_keeps_validation_and_signature(mail_context, monkeypatch, tool, kwargs):
    monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", "false")
    assert getattr(server, tool)(**kwargs)["success"]
    assert mail_context[0]["account_id"] == 1
    assert mail_context[0]["body"] == "Hello signed"
    assert policy.store().list_pending() == []
    assert getattr(server, tool)(**kwargs, attachments=["missing.pdf"])["status"] == "error"
    assert len(mail_context) == 1


@pytest.mark.parametrize("value", [None, "true", "0", "invalid"])
def test_approval_is_default(mail_context, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", value)
    assert server.send_mail("recipient@example.com", "Test", "Hello")["status"] == "approval_required"
    assert mail_context == []


def test_pending_request_still_requires_approval(mail_context, monkeypatch):
    pending = server.send_mail("recipient@example.com", "Test", "Hello")
    monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", "false")
    result = server.send_mail("other@example.com", "Changed", "Changed",
                              request_id=pending["request_id"])
    assert result["status"] == "awaiting_approval"
    assert mail_context == []
    policy.store().approve(pending["request_id"])
    server.send_mail("other@example.com", "Changed", "Changed", request_id=pending["request_id"])
    assert mail_context[0]["to"] == "recipient@example.com"


def test_immediate_mail_respects_dry_run(mail_context, monkeypatch):
    monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", "false")
    monkeypatch.setenv("ADE_MAIL_DRYRUN", "1")
    assert server.send_mail("recipient@example.com", "Test", "Hello")["dryrun"]
    assert mail_context == []


def test_other_writes_remain_gated(mail_context, monkeypatch):
    monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", "false")
    assert server.delete_message("42")["status"] == "approval_required"
    assert mail_context == []


def test_immediate_failure_is_audited(mail_context, monkeypatch):
    monkeypatch.setenv("GIGAMAIL_SEND_REQUIRE_APPROVAL", "false")
    events = []
    monkeypatch.setattr(server, "audit", lambda *args, **kw: events.append(args))

    def fail(**kwargs):
        raise RuntimeError("Provider unavailable")

    monkeypatch.setattr(server.mail_router, "send_message", fail)
    with pytest.raises(RuntimeError, match="Provider unavailable"):
        server.send_mail("recipient@example.com", "Test", "Hello")
    assert events[-1][2] == "error"
