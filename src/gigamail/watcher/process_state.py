"""Stato del processo watcher: pid, heartbeat, "e' vivo?".

La stessa risposta serve a tre chiamanti — console, CLI e l'attivita'
pianificata — e tre copie divergerebbero: basta che una dica 'fermo'
quando e' vivo e si ritrovano due watcher sulle stesse regole.
"""
import os
import time
from typing import Any, Dict

from gigamail.core import rules as rules_mod

from .log import logger


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, AttributeError, ValueError):
        return False


_MY_STAMP = ""


def my_code_stamp() -> str:
    """The sources this watcher process loaded, computed once: after an
    update the files on disk differ, and the backend's guard restarts
    the watcher so the new code runs."""
    global _MY_STAMP
    if not _MY_STAMP:
        try:
            from gigamail.core.code_stamp import code_stamp
            _MY_STAMP = code_stamp()
        except Exception:
            _MY_STAMP = "?"
    return _MY_STAMP


def heartbeat(interval: int) -> None:
    """Stato del processo per la console: pid, intervallo e ultimo
    giro, in rules.db (kv). 'attivo' = heartbeat recente."""
    try:
        rs = rules_mod.store()
        rs.kv_set("watch_pid", str(os.getpid()))
        rs.kv_set("watch_interval", str(interval))
        rs.kv_set("watch_heartbeat", str(time.time()))
        rs.kv_set("watch_stamp", my_code_stamp())
    except Exception as e:
        # Senza heartbeat la console dira' "fermo" mentre il watcher gira,
        # e chi lancia il task ne avviera' un secondo: va detto.
        logger.warning("heartbeat non scritto: %s", e)


def running_state() -> Dict[str, Any]:
    """C'e' un watcher vivo? Il watcher registra pid, intervallo e
    battito a ogni giro: un pid ancora esistente ma fermo da piu' di
    tre giri e' un processo morto male, non un watcher."""
    rs = rules_mod.store()
    hb = float(rs.kv_get("watch_heartbeat", "0") or 0)
    interval = int(rs.kv_get("watch_interval", "60") or 60)
    pid = int(rs.kv_get("watch_pid", "0") or 0)
    age = time.time() - hb if hb else None
    alive = pid_alive(pid)
    running = alive and age is not None and age < max(interval * 3, 90)
    return {"running": running, "pid": pid if alive else None,
            "interval": interval,
            "code_stamp": rs.kv_get("watch_stamp", "") or "",
            "last_tick_age_seconds": int(age) if age is not None else None,
            "active_rules": len(rs.active())}
