"""Credential-free setup diagnostics. Network probes are explicitly requested."""
import shutil
import sys

from gigamail import __version__
from gigamail.core import accounts, mail_router, send_config
from gigamail.core.data_paths import app_root, data_root


def report(check_connectivity=False):
    rows = []
    for account in accounts.get_accounts():
        aid = account["id"]
        detail = accounts.get_account_by_id(aid) or {}
        data = detail.get("data") or {}
        configured = bool(data.get("token_cache") if account.get("type", "microsoft") == "microsoft"
                          else data.get("password") or account.get("type") == "demo")
        item = {"account_id": aid, "provider": account.get("type", "microsoft"),
                "credentials_configured": configured, "send_mode": send_config.status(aid),
                "connectivity": "not_checked"}
        if check_connectivity:
            try:
                mail_router.list_folders(account_id=aid)
                item["connectivity"] = "ok"
            except Exception:
                # Provider exceptions may include sensitive content; do not dump them.
                item["connectivity"] = "failed"
        rows.append(item)
    return {"version": __version__, "python": sys.executable,
            "server_executable": shutil.which("gigamail-server"),
            "app_root": str(app_root()), "data_root": str(data_root()), "send_mode": send_config.status(),
            "accounts": rows,
            "note": "Configured credentials are not proof of an active provider login; use --connectivity to check"}
