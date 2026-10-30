"""Failure injection and concurrency coverage for mail reliability changes."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from gigamail import cli, diagnostics, policy, server
from gigamail.core import (
    atomic_file,
    attachments,
    http_transport,
    imap_client,
    mail,
    mail_router,
    outbound,
    reply_guard,
    rules,
    send_config,
)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    operations = outbound.OutboundStore(tmp_path / "outbound.db")
    rs = rules.RuleStore(tmp_path / "rules.db")
    policy.set_store(policy.ApprovalStore(tmp_path / "approvals.db"))
    rules.set_store(rs)
    monkeypatch.setattr(outbound, "store", lambda: operations)
    monkeypatch.delenv("ADE_MAIL_DRYRUN", raising=False)
    monkeypatch.delenv(send_config.ENV, raising=False)
    yield operations, rs
    policy.set_store(None)
    rules.set_store(None)


def test_concurrent_retries_claim_once_and_survive_restart(stores):
    operations, _ = stores
    started, release = threading.Event(), threading.Event()
    calls = []

    def submit():
        calls.append(True)
        started.set()
        assert release.wait(5)
        return {"success": True}

    with ThreadPoolExecutor() as pool:
        first = pool.submit(operations.execute, 1, "same-key", {"body": "Hello"}, submit)
        assert started.wait(5)
        second = operations.execute(1, "same-key", {"body": "Hello"}, submit)
        assert second["status"] == "unknown" and not second["retryable"]
        release.set()
        accepted = first.result(5)
    restarted = outbound.OutboundStore(operations.path)
    retry = restarted.execute(1, "same-key", {"body": "Hello"}, lambda: pytest.fail("duplicate"))
    assert retry["operation_id"] == accepted["operation_id"]
    assert retry["deduplicated"] and retry["success"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="different email"):
        restarted.execute(1, "same-key", {"body": "Changed"}, submit)
    # A separate account owns a separate key space.
    assert restarted.execute(2, "same-key", {}, lambda: {"success": True})["success"]


def test_abandoned_claim_is_not_resent(stores):
    operations, _ = stores
    with operations._conn() as conn:
        # Simulate a process ending after claiming, before recording a result.
        import hashlib
        fingerprint = hashlib.sha256(json.dumps({}, sort_keys=True).encode()).hexdigest()
        conn.execute("INSERT INTO outbound VALUES (?,?,?,'sending',NULL,0)", (1, "crashed", fingerprint))
    restarted = outbound.OutboundStore(operations.path)
    result = restarted.execute(1, "crashed", {}, lambda: pytest.fail("must not resend"))
    assert result["status"] == "unknown"


def test_exception_and_result_persistence_failure_keep_claim(stores, monkeypatch):
    operations, _ = stores
    def interrupted():
        raise requests.ReadTimeout("Connection lost after submission")
    assert operations.execute(1, "uncertain", {}, interrupted)["status"] == "unknown"
    assert operations.get(1, "uncertain")["state"] == "unknown"
    assert operations.execute(1, "uncertain", {}, lambda: pytest.fail("retry"))["status"] == "unknown"
    monkeypatch.setattr(operations, "_finish", lambda *args: False)
    accepted = operations.execute(1, "disk-full", {}, lambda: {"success": True})
    assert accepted["success"] and accepted["tracking_warning"]
    assert operations.execute(1, "disk-full", {}, lambda: pytest.fail("retry"))["status"] == "unknown"


def test_immediate_post_send_audit_failure_preserves_success(monkeypatch):
    monkeypatch.setenv(send_config.ENV, "false")
    monkeypatch.setattr(policy, "dry_run_active", lambda: False)
    def audit(*args, **kwargs):
        if args[2] == "executed":
            raise OSError("Disk full")
    monkeypatch.setattr(server, "audit", audit)
    result = server._execute_mail("send_mail", {}, None, preview_fn=lambda: {},
                                  execute_fn=lambda args: {"success": True})
    assert result["success"] and result["audit_warning"]


def test_approved_post_send_persistence_failure_preserves_success(stores, monkeypatch):
    rid = policy.store().create("send_mail", {"account_id": 1}, {})
    policy.store().approve(rid)
    monkeypatch.setattr(policy.store(), "record_outcome", Mock(side_effect=OSError("Disk full")))
    result = policy.execute_dangerous("send_mail", {}, rid, lambda: {}, lambda args: {"success": True})
    assert result["success"] and result["audit_warning"]
    assert policy.store().get(rid)["status"] == policy.EXECUTED


def test_pending_and_handled_replies_are_scoped(stores):
    _, rs = stores
    rid = policy.store().create("reply_mail", {"account_id": 2, "folder": "Archive", "message_id": "42"}, {})
    assert reply_guard.existing_reply("42", account_id=1, folder="Archive") is None
    assert reply_guard.existing_reply("42", account_id=2, folder="inbox") is None
    assert reply_guard.existing_reply("42", account_id=2, folder="Archive")["request_id"] == rid
    rs.record("rule", 1, "42", "sender@example.com", "sent", folder="INBOX", uidvalidity="100")
    rs.record("rule", 2, "42", "sender@example.com", "sent", folder="INBOX", uidvalidity="100")
    rs.record("rule", 1, "42", "sender@example.com", "sent", folder="Archive", uidvalidity="100")
    assert len(rs.replies_to("42")) == 3
    assert reply_guard.existing_reply("42", account_id=1, folder="inbox", uidvalidity="200") is None
    assert reply_guard.existing_reply("42", account_id=1, folder="inbox", uidvalidity="100")["state"] == "sent"
    rs.set_status("rule", "42", "failed", account_id=1, folder="Archive")
    assert rs.get_handled("rule", "42", account_id=2, folder="INBOX")["status"] == "sent"


def test_legacy_handled_schema_migrates_without_losing_records(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE handled (
            rule_id TEXT NOT NULL, account_id INTEGER NOT NULL, message_id TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, reason TEXT,
            request_id TEXT, ts REAL NOT NULL, PRIMARY KEY(rule_id,message_id))""")
        conn.execute("INSERT INTO handled VALUES ('old',1,'42','sender@example.com','sent',NULL,NULL,0)")
    rs = rules.RuleStore(path)
    assert rs.get_handled("old", "42")["folder"] == "INBOX"
    rs.record("old", 2, "42", "sender@example.com", "sent", folder="Archive")
    assert len(rs.replies_to("42")) == 2


def test_graph_reads_do_not_write_and_rejected_patch_is_not_success(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"id": "opaque-id", "hasAttachments": False}
    monkeypatch.setattr(mail, "_headers", lambda: {})
    monkeypatch.setattr(mail.requests, "get", lambda *args, **kwargs: response)
    patch = Mock(side_effect=AssertionError("A read must not write"))
    monkeypatch.setattr(mail.requests, "patch", patch)
    assert mail.get_message("opaque-id")["id"] == "opaque-id"
    patch.assert_not_called()
    rejection = Mock()
    rejection.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
    monkeypatch.setattr(mail.requests, "patch", lambda *args, **kwargs: rejection)
    monkeypatch.setattr(mail_router, "_account", lambda *args: {"type": "microsoft"})
    assert not mail_router.set_read_status(1, "opaque-id")


def test_graph_attached_reply_keeps_original_thread_and_fixed_recipients(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=201 if url.endswith(('createReply', 'attachments')) else 202,
                               headers={}, text="", json=lambda: {"id": "draft"}, raise_for_status=lambda: None)
    monkeypatch.setattr(mail, "_headers", lambda: {})
    monkeypatch.setattr(mail.requests, "post", post)
    changed = []
    monkeypatch.setattr(mail.requests, "patch", lambda url, **kwargs:
                        changed.append(kwargs["json"]) or SimpleNamespace(raise_for_status=lambda: None))
    result = mail.send_message("recipient@example.com", "Re: Test", "Hello", reply_to_id="original",
                               attachments=[{"name": "test.txt", "data_b64": "dGVzdA==", "type": "text/plain"}])
    assert result["success"]
    assert [url.rsplit('/', 1)[-1] for url, _ in calls] == ['createReply', 'attachments', 'send']
    assert '/original/' in calls[0][0]
    assert changed[0]["toRecipients"] == [{"emailAddress": {"address": "recipient@example.com"}}]


@pytest.mark.parametrize("attachment", [
    {"name": "bad.txt", "data_b64": "%%%", "type": "text/plain"},
    {"name": "bad.txt", "type": "text/plain"},
    {"name": "bad.txt", "data_b64": "dGVzdA==", "type": "invalid"},
])
def test_bad_attachment_never_contacts_provider(monkeypatch, attachment):
    monkeypatch.setattr(imap_client.smtplib, "SMTP_SSL", Mock(side_effect=AssertionError("must not connect")))
    monkeypatch.setattr(mail.requests, "post", Mock(side_effect=AssertionError("must not connect")))
    with pytest.raises(ValueError, match="bad.txt"):
        imap_client.send_message("smtp.example.com", 465, "sender@example.com", "test",
                                 "recipient@example.com", "Test", "Hello", attachments=[attachment])
    with pytest.raises(ValueError, match="bad.txt"):
        mail.send_message("recipient@example.com", "Test", "Hello", attachments=[attachment])
    assert attachments.validate_wire_attachment({"name": "empty.txt", "data_b64": ""})[0] == b""


def test_explicit_invalid_account_never_falls_back(monkeypatch):
    monkeypatch.setattr(mail_router.acc, "get_active_account", lambda: pytest.fail("fallback"))
    with pytest.raises(ValueError, match="Invalid explicit account"):
        mail_router.get_messages(account_id="wrong")


def test_transport_reuses_read_pool_and_never_retries_writes(monkeypatch):
    local = threading.local()
    monkeypatch.setattr(http_transport, "_local", local)
    clients = []
    def session():
        client = Mock()
        clients.append(client)
        return client
    monkeypatch.setattr(http_transport.requests, "Session", session)
    http_transport.get("https://example.com/a", headers={"Authorization": "account-a"})
    http_transport.get("https://example.com/b", headers={"Authorization": "account-b"})
    http_transport.post("https://example.com/send", json={})
    assert len(clients) == 2
    assert clients[0].request.call_count == 2
    assert clients[0].request.call_args.kwargs["timeout"] == (5, 30)
    assert clients[1].mount.call_args.args[1].max_retries.total == 0
    assert clients[0].mount.call_args.args[1].max_retries.allowed_methods == frozenset({"GET", "HEAD"})


def test_atomic_write_failure_keeps_previous_file_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    atomic_file.write_text(path, '{"version":1}')
    monkeypatch.setattr(atomic_file.os, "replace", Mock(side_effect=OSError("Disk full")))
    with pytest.raises(OSError):
        atomic_file.write_text(path, '{"version":2}')
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_mode_precedence_cli_and_diagnostics_hide_credentials(monkeypatch, capsys):
    settings = {}
    monkeypatch.delenv(send_config.ENV, raising=False)
    monkeypatch.setattr(send_config.accounts, "get_setting", lambda key, default="": settings.get(key, default))
    monkeypatch.setattr(send_config.accounts, "set_setting", lambda key, value: settings.__setitem__(key, value))
    assert cli.main(["send-mode", "immediate"]) == 0
    assert not send_config.requires_approval(1)
    send_config.configure("approval", 1)
    assert send_config.requires_approval(1) and not send_config.requires_approval(2)
    monkeypatch.setenv(send_config.ENV, "true")
    assert send_config.requires_approval(2)
    monkeypatch.setattr(diagnostics.accounts, "get_accounts", lambda: [{"id": 1, "type": "imap"}])
    monkeypatch.setattr(diagnostics.accounts, "get_account_by_id", lambda aid: {"data": {"password": "DO_NOT_PRINT"}})
    monkeypatch.setattr(diagnostics.mail_router, "list_folders", lambda **kwargs: pytest.fail("network not requested"))
    assert cli.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in output and '"not_checked"' in output


def test_compact_summary_avoids_message_payloads(stores, monkeypatch):
    from gigamail.http_api.approvals import status_summary
    monkeypatch.setattr(send_config.accounts, "get_accounts", lambda: [])
    monkeypatch.setattr(send_config.accounts, "get_setting", lambda key, default="": default)
    for index in range(10):
        policy.store().create("send_mail", {"body": "x" * 10000, "subject": str(index)},
                              {"body": "x" * 10000})
    legacy_size = len(json.dumps(policy.store().list_pending()))
    summary = status_summary()
    assert summary["pending_count"] == 10
    assert "body" not in json.dumps(summary)
    assert len(json.dumps(summary)) < legacy_size / 100


def test_token_cache_updates_merge_concurrent_records(tmp_path, monkeypatch):
    from gigamail.core import auth
    path = tmp_path / "tokens.json"
    monkeypatch.setattr(auth, "TOKEN_PATH", str(path))
    original = {"AccessToken": {"one": "old-one", "two": "old-two"}}
    atomic_file.write_text(path, json.dumps(original))
    first = SimpleNamespace(has_state_changed=True, _gigamail_original=original,
                            serialize=lambda: json.dumps({"AccessToken": {"one": "new-one", "two": "old-two"}}))
    second = SimpleNamespace(has_state_changed=True, _gigamail_original=original,
                             serialize=lambda: json.dumps({"AccessToken": {"one": "old-one", "two": "new-two"}}))
    auth._save_cache(first)
    auth._save_cache(second)
    assert json.loads(path.read_text()) == {"AccessToken": {"one": "new-one", "two": "new-two"}}


def test_smtp_disconnect_after_data_is_unknown_and_bounded(monkeypatch):
    options = []
    class SMTP:
        def __init__(self, *args, **kwargs):
            options.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def login(self, *args):
            pass
        def sendmail(self, *args):
            raise TimeoutError("Reply lost after DATA")
    monkeypatch.setattr(imap_client.smtplib, "SMTP_SSL", SMTP)
    result = imap_client.send_message("smtp.example.com", 465, "sender@example.com", "test",
                                      "recipient@example.com", "Test", "Hello")
    assert options[0]["timeout"] == 30
    assert not result["success"] and result["submission_state"] == "unknown"
    assert result["provider_result"]["accepted"] is None


def test_completion_preserves_acceptance_when_approval_outcome_write_fails(stores, monkeypatch):
    from gigamail import completion
    rid = policy.store().create("send_mail", {"account_id": 1}, {})
    policy.store().approve(rid)
    monkeypatch.setattr(policy.store(), "record_outcome", Mock(side_effect=OSError("Disk full")))
    monkeypatch.setitem(server.APPROVED_EXECUTORS, "send_mail", lambda args: {"success": True})
    result = completion.complete(rid)
    assert result["outcome"] == "ok" and result["warning"]


def test_migration_recovers_builtin_reply_folder_from_approval(stores, tmp_path):
    rid = policy.store().create("reply_mail", {"account_id": 1, "folder": "Archive", "message_id": "42"}, {})
    path = tmp_path / "legacy-scoped.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE handled (
            rule_id TEXT NOT NULL, account_id INTEGER NOT NULL, message_id TEXT NOT NULL,
            sender TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, reason TEXT,
            request_id TEXT, ts REAL NOT NULL, PRIMARY KEY(rule_id,message_id))""")
        conn.execute("INSERT INTO handled VALUES ('builtin',1,'42','sender@example.com','awaiting_approval',NULL,?,0)", (rid,))
    # Simultaneous server/watcher startup must serialize migrations.
    with ThreadPoolExecutor() as pool:
        stores_after = list(pool.map(lambda _: rules.RuleStore(path), range(2)))
    assert stores_after[0].get_handled("builtin", "42", account_id=1, folder="Archive")["request_id"] == rid


def test_per_account_inherit_restores_global_default(monkeypatch):
    settings = {}
    monkeypatch.delenv(send_config.ENV, raising=False)
    monkeypatch.setattr(send_config.accounts, "get_setting", lambda key, default="": settings.get(key, default))
    monkeypatch.setattr(send_config.accounts, "set_setting", lambda key, value: settings.__setitem__(key, value))
    send_config.configure("immediate")
    send_config.configure("approval", 1)
    assert send_config.requires_approval(1)
    send_config.configure("inherit", 1)
    assert not send_config.requires_approval(1)


def test_mcp_immediate_and_approved_paths_share_outbound_tracking(stores, monkeypatch):
    account = {"id": 1, "type": "demo", "email": "sender@example.com"}
    monkeypatch.setattr(server.core_accounts, "get_active_account", lambda: account)
    monkeypatch.setattr(server.core_accounts, "get_account_by_id", lambda aid: account)
    monkeypatch.setattr(server.signature, "apply", lambda aid, body: body)
    monkeypatch.setattr(policy, "notify_approval_requested", lambda *args, **kwargs: None)
    backend = Mock(return_value={"success": True, "provider_result": {"accepted": 1}})
    monkeypatch.setattr(mail_router, "_send_backend", backend)
    monkeypatch.setenv(send_config.ENV, "false")
    first = server.send_mail("recipient@example.com", "Test", "Hello", idempotency_key="end-to-end")
    again = server.send_mail("recipient@example.com", "Test", "Hello", idempotency_key="end-to-end")
    assert first["operation_id"] == again["operation_id"] and again["deduplicated"]
    monkeypatch.setenv(send_config.ENV, "true")
    pending = server.send_mail("recipient@example.com", "Test", "Hello", idempotency_key="end-to-end")
    policy.store().approve(pending["request_id"])
    approved = server.send_mail("ignored@example.com", "Ignored", "Ignored", request_id=pending["request_id"])
    assert approved["deduplicated"] and backend.call_count == 1


def test_watcher_status_failure_preserves_known_provider_result(stores, monkeypatch):
    from gigamail.watcher import execution
    _, rs = stores
    rid = policy.store().create("reply_mail", {"account_id": 1, "message_id": "42"}, {})
    policy.store().approve(rid)
    rs.record("rule", 1, "42", "sender@example.com", "awaiting_approval", request_id=rid)
    monkeypatch.setattr(execution, "_execute_reply", lambda *args: {"success": True})
    monkeypatch.setattr(rs, "set_status", Mock(side_effect=OSError("Disk full")))
    result = execution.complete_row(rs.find_by_request(rid))
    assert result["status"] == "sent" and result["tracking_warning"]
