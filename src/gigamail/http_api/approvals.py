"""Approvazioni: il canale fuori banda (Hello / Touch ID) e l'audit."""
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request

from gigamail import policy

from .common import _who

router = APIRouter()


# ── APPROVAZIONI (il canale fuori banda) ─────────────────────────────
# Questi endpoint sono il punto in cui un UMANO autorizza un'azione
# distruttiva richiesta dall'agente. Vivono qui, dietro il token della
# console, e non esistono come tool MCP: l'agente puo' creare richieste
# e leggerne lo stato, ma non puo' approvarle.

def _completed_by(request_id: str) -> str:
    """Who sends an approved request: "watcher" for a draft born from a
    rule (the watcher executes it on its next cycle), "agent" for a tool
    request (the agent that asked completes it with the request_id). The
    console says this after an approval, instead of implying "sent"."""
    try:
        from gigamail.core import rules as rules_mod
        if rules_mod.store().find_by_request(request_id):
            return "watcher"
    except Exception:
        pass
    return "agent"


def _with_origin(rec):
    rec["completed_by"] = _completed_by(rec["request_id"])
    return rec


def _with_notifications(recs):
    """Each request with the notifications sent for it, and the ones that
    failed: a draft that expired at night with the Telegram send broken
    should say so where the user looks."""
    recs = [_with_origin(r) for r in recs]
    try:
        seen = policy.notifications_of([r["request_id"] for r in recs])
    except Exception:
        seen = {}
    for r in recs:
        r["notified"] = seen.get(r["request_id"], [])
    return recs


@router.get("/approvals")
def list_approvals():
    """Richieste in attesa di approvazione umana."""
    return _with_notifications(policy.store().list_pending())


@router.get("/approvals/recent")
def recent_approvals(hours: float = 24):
    """Requests decided (or expired undecided) in the last `hours`: the
    console's "recently decided" list, with the execution outcome."""
    hours = max(0.0, min(float(hours), 24 * 30))
    since = time.time() - hours * 3600
    return _with_notifications(policy.store().list_decided(since))


@router.get("/approvals/{request_id}")
def get_approval(request_id: str):
    rec = policy.store().get(request_id)
    if not rec:
        raise HTTPException(404, "Richiesta inesistente")
    return rec



@router.post("/approvals/{request_id}/approve")
def approve_request(request_id: str, request: Request = None):
    """Approva SOLO dopo una verifica dell'utente fisico (Windows Hello /
    Touch ID). Il token di sessione della console sta in un file: un
    processo puo' leggerlo e chiamare questo endpoint. Il prompt OS e'
    cio' che un processo non puo' superare — quindi sta qui, non nella
    finestra. Senza backend di consenso: 503, nessuna approvazione.

    Un clic su Approva in una finestra della console porta il segreto del
    clic (console_click), che il file del token non contiene: e' una
    persona davanti alla console, come per l'invio, e non apre il prompt
    (che su Windows poteva aprirsi dietro le finestre e lasciare il bottone
    fermo). Resta il prompt se l'utente l'ha chiesto anche per la console."""
    from gigamail import consent

    from . import console_click
    rec = policy.store().get(request_id)
    if not rec:
        raise HTTPException(404, "Richiesta inesistente")
    clic = (request is not None and console_click.is_console_click(request.headers)
            and not console_click.send_requires_hello())
    if not clic:
        reason = f"GigaMail: approvare {rec['tool']} ({request_id})?"
        try:
            ok = consent.require_human(reason)
        except consent.ConsentUnavailable as e:
            raise HTTPException(503, str(e)) from e
        if not ok:
            raise HTTPException(403, "Verifica utente non superata o annullata")
    by = f"{_who()} (console click)" if clic else _who()
    if not policy.store().approve(request_id, by=by):
        raise HTTPException(409, "Richiesta non approvabile (già decisa o scaduta)")
    return {"success": True, "request_id": request_id, "status": "approved",
            "completed_by": _completed_by(request_id)}


@router.post("/approvals/{request_id}/reject")
def reject_request(request_id: str):
    if not policy.store().reject(request_id, by=_who()):
        raise HTTPException(409, "Richiesta non rifiutabile (già decisa o scaduta)")
    return {"success": True, "request_id": request_id, "status": "rejected"}


@router.get("/audit")
def audit_log(limit: int = 100):
    """Ultime azioni compiute dall'agente (per la vista console)."""
    from gigamail.core.data_paths import app_root
    path = os.path.join(str(app_root()), "agent_audit.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out
