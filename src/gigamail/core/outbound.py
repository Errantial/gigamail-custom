"""Durable claims for outgoing mail. Unknown submissions are never resent."""
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .data_paths import data_root

logger = logging.getLogger(__name__)
current_key = ContextVar("outbound_operation_key", default=None)


class OutboundStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS outbound (
                account_id INTEGER NOT NULL, operation_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                result TEXT, updated REAL NOT NULL,
                PRIMARY KEY(account_id, operation_id))""")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, account_id, operation_id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM outbound WHERE account_id=? AND operation_id=?",
                               (account_id, operation_id)).fetchone()
        return dict(row) if row else None

    def execute(self, account_id, operation_id, payload, execute):
        operation_id = str(uuid.uuid4()) if operation_id is None else operation_id
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                               default=str).encode()).hexdigest()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM outbound WHERE account_id=? AND operation_id=?",
                               (account_id, operation_id)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ValueError("idempotency_key was already used for a different email")
                if row["result"]:
                    return dict(json.loads(row["result"]), deduplicated=True)
                return {"success": False, "status": "unknown", "operation_id": operation_id,
                        "deduplicated": True, "retryable": False,
                        "error": "Submission is in progress or its outcome is unknown; do not resend"}
            conn.execute("INSERT INTO outbound VALUES (?,?,?,'sending',NULL,?)",
                         (account_id, operation_id, fingerprint, time.time()))
        try:
            result = execute()
        except Exception as exc:
            # An exception may occur after provider acceptance. Keep the claim.
            uncertain = {
                "success": False, "status": "unknown", "operation_id": operation_id,
                "retryable": False, "submission_state": "unknown", "error": str(exc)}
            self._finish(account_id, operation_id, "unknown", uncertain)
            from requests import RequestException
            if isinstance(exc, (RequestException, TimeoutError, ConnectionError)):
                return uncertain
            raise
        result = dict(result) if isinstance(result, dict) else {"success": bool(result)}
        state = result.get("submission_state")
        if state not in {"accepted", "partially_accepted", "failed", "unknown"}:
            refused = (result.get("provider_result") or {}).get("refused")
            state = ("partially_accepted" if refused else "accepted") if result.get("success") else "failed"
        result.update(operation_id=operation_id, submission_state=state)
        if state == "unknown":
            result.update(status="unknown", retryable=False)
        if not self._finish(account_id, operation_id, state, result):
            result["tracking_warning"] = "Provider result known, but local tracking could not be updated"
        return result

    def _finish(self, account_id, operation_id, state, result):
        try:
            with self._conn() as conn:
                conn.execute("UPDATE outbound SET state=?, result=?, updated=? WHERE account_id=? AND operation_id=?",
                             (state, json.dumps(result), time.time(), account_id, operation_id))
            return True
        except Exception:
            logger.exception("Cannot persist outbound outcome")
            return False


def store():
    return OutboundStore(data_root() / "outbound.db")


def reply_key(message_id, folder="inbox", uidvalidity=""):
    from .rules import normalize_folder
    scope = json.dumps([str(message_id), normalize_folder(folder), str(uidvalidity)], separators=(",", ":"))
    return "reply:" + hashlib.sha256(scope.encode()).hexdigest()


@contextmanager
def operation_key(key):
    token = current_key.set(key)
    try:
        yield
    finally:
        current_key.reset(token)
