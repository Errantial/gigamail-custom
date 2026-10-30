"""Operator-controlled MCP send mode, with environment taking precedence."""
import os

from . import accounts

ENV = "GIGAMAIL_SEND_REQUIRE_APPROVAL"


def requires_approval(account_id=None):
    if ENV in os.environ:
        return os.environ[ENV].strip().lower() != "false"
    if account_id is not None:
        value = accounts.get_setting(f"send_require_approval:{int(account_id)}", "")
        if value:
            return value != "false"
    return accounts.get_setting("send_require_approval", "true") != "false"


def configure(mode, account_id=None):
    key = "send_require_approval" + (f":{int(account_id)}" if account_id is not None else "")
    accounts.set_setting(key, "" if mode == "inherit" else ("false" if mode == "immediate" else "true"))


def status(account_id=None):
    return {"mode": "approval" if requires_approval(account_id) else "immediate",
            "account_id": account_id, "environment_override": ENV in os.environ}
