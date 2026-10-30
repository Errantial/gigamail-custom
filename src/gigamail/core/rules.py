# GigaMail — mail for your AI agent
# Copyright (C) 2026 Adecubed
# Licensed under the GNU AGPL v3 or later. See LICENSE.
"""Regole di risposta (0.2): semi-auto e auto reply.

Una regola dice: per le mail che arrivano DA questi mittenti (o IN questa
cartella), prepara una risposta pescando SOLO da questi documenti, in
questo stile. `mode` decide il finale:
  semi  la bozza diventa una normale richiesta di approvazione (pending,
        notificata): l'umano approva con Hello, poi parte.
  auto  la richiesta nasce gia' approvata, decided_by "automode:<rule_id>":
        la pre-approvazione E' la regola, che l'umano ha creato dietro
        Windows Hello / Touch ID, con scadenza e tetto giornaliero.

Chi puo' creare o riattivare una regola: SOLO la CLI o la console, dietro
verifica dell'utente fisico (consent.require_human). Nessun tool MCP tocca
questo modulo: un'istruzione iniettata in una mail non puo' dire all'agente
"attiva l'automode" — l'agente non ha il tool.

Lo stato vive in .rules.db sotto app_root(), accanto ad approvals.db:
processi diversi (watcher, CLI, console) condividono SQLite, non memoria.
"""
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def normalize_folder(folder):
    value = str(folder or "INBOX")
    return "INBOX" if value.lower() == "inbox" else value


def _scope_sql(account_id=None, folder=None, uidvalidity=None):
    clauses, values = [], []
    if account_id is not None:
        clauses.append("account_id=?")
        values.append(int(account_id))
    if folder is not None:
        clauses.append("folder=?")
        values.append(normalize_folder(folder))
    if uidvalidity is not None:
        clauses.append("uidvalidity=?")
        values.append(str(uidvalidity))
    return (" AND " + " AND ".join(clauses) if clauses else ""), values


def handled_scope(row):
    return {"account_id": row["account_id"], "folder": row.get("folder") or "INBOX",
            "uidvalidity": row.get("uidvalidity") or ""}


# Trigger possibili, dal meno al piu' rischioso:
#   senders  lista esplicita di indirizzi (match sul From autenticato)
#   folder   una cartella: mittente arbitrario — e' il trigger da cui
#            first_contact:semi e le barriere anti-spam difendono
TRIGGER_KINDS = ("senders", "folder")
MODES = ("semi", "auto")

# Default prudenti. La scadenza e' OBBLIGATORIA: una regola eterna e' una
# pre-approvazione eterna, e nessuna approvazione dovrebbe esserlo.
DEFAULT_EXPIRY_DAYS = 30
DEFAULT_DAILY_CAP = 10
DEFAULT_COOLDOWN_HOURS = 4
# Raffica: piu' di BURST_MAX match sulla stessa regola in BURST_WINDOW
# secondi → la regola si pausa da sola (fail-closed) e riparte solo con
# Hello. Un attacco manda 5 mail, non 200.
BURST_MAX = 5
BURST_WINDOW_SECONDS = 600


def _db_path() -> Path:
    from gigamail.core.data_paths import app_root
    return app_root() / ".rules.db"


class _ClosingConnection(sqlite3.Connection):
    """Come in policy.py: l'uscita dal `with` chiude la connessione,
    non solo commit/rollback — senza, su Windows il file resta lockato
    fino al GC."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


class RuleStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = str(path or _db_path())
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10,
                               factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        # WAL (set once, kept by the file) and synchronous=NORMAL (per
        # connection): the watcher writes its heartbeat many times a tick,
        # and a full fsync per write stalled a slow Windows disk (CI timed
        # out inside kv_set). Durable across a crash of the process; a
        # power loss can lose the last heartbeat, which is nothing.
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    rule_id           TEXT PRIMARY KEY,
                    account_id        INTEGER NOT NULL,
                    trigger_kind      TEXT NOT NULL,
                    trigger_values    TEXT NOT NULL,
                    reply_style       TEXT NOT NULL DEFAULT '',
                    doc_paths         TEXT NOT NULL DEFAULT '[]',
                    mode              TEXT NOT NULL,
                    first_contact     TEXT NOT NULL DEFAULT 'semi',
                    daily_cap         INTEGER NOT NULL,
                    cooldown_hours    REAL NOT NULL,
                    expires_at        REAL NOT NULL,
                    created_at        REAL NOT NULL,
                    created_by        TEXT NOT NULL,
                    hello_verified_at REAL NOT NULL,
                    paused            INTEGER NOT NULL DEFAULT 0,
                    pause_reason      TEXT,
                    -- 1 = rispondi all'indirizzo scritto NEL CORPO,
                    -- non al mittente. Serve ai portali (idealista,
                    -- immobiliare): li' il From e' un relay e la
                    -- persona vera sta dentro il messaggio. Default 0:
                    -- l'indirizzamento fisso resta la norma.
                    reply_to_body_address INTEGER NOT NULL DEFAULT 0,
                    -- copie fisse e allegati fissi della regola: i
                    -- secondi sono NOMI di file registrati
                    -- nell'identity, risolti al momento della bozza e
                    -- mostrati nell'anteprima che l'umano approva.
                    cc                TEXT NOT NULL DEFAULT '[]',
                    attachments       TEXT NOT NULL DEFAULT '[]'
                )
            """)
            # Ogni mail che una regola ha guardato, con l'esito: e' insieme
            # l'idempotenza del watcher (mai due bozze per la stessa mail),
            # il contatore del tetto giornaliero, il cooldown per mittente e
            # la finestra del rilevatore di raffica.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS handled (
                    rule_id    TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    sender     TEXT NOT NULL DEFAULT '',
                    status     TEXT NOT NULL,
                    reason     TEXT,
                    request_id TEXT,
                    ts         REAL NOT NULL,
                    PRIMARY KEY (rule_id, message_id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_handled_rule_ts"
                " ON handled (rule_id, ts)")
            # The people a rule has answered: a reply from one of them
            # belongs to that rule, whatever the trigger says. A lead
            # from the site form was answered; the person wrote back from
            # her own address, and no rule matched her.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    rule_id    TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    address    TEXT NOT NULL,
                    ts         REAL NOT NULL,
                    PRIMARY KEY (rule_id, address)
                )
            """)
            # feedback dell'umano per "rifiuta e riprova con modifica"
            cols = {r[1] for r in conn.execute("PRAGMA table_info(handled)")}
            if "feedback" not in cols:
                conn.execute("ALTER TABLE handled ADD COLUMN feedback TEXT")
            # When the mail ARRIVED: the burst guard counts by this, not by
            # when the watcher got to it (a night's backlog read in one
            # tick is not a burst).
            if "received_ts" not in cols:
                conn.execute("ALTER TABLE handled ADD COLUMN received_ts REAL")
            # Scope IMAP UIDs to their mailbox and folder; preserve old records.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(handled)")}
            if "folder" not in cols:
                conn.execute("ALTER TABLE handled ADD COLUMN folder TEXT NOT NULL DEFAULT 'INBOX'")
                for rule in conn.execute("SELECT rule_id, trigger_kind, trigger_values FROM rules").fetchall():
                    if rule["trigger_kind"] == "folder":
                        values = json.loads(rule["trigger_values"])
                        if values:
                            conn.execute("UPDATE handled SET folder=? WHERE rule_id=?",
                                         (normalize_folder(values[0]), rule["rule_id"]))
                from gigamail import policy
                for handled in conn.execute("SELECT rule_id, account_id, message_id, request_id FROM handled WHERE request_id IS NOT NULL").fetchall():
                    rec = policy.store().get(handled["request_id"])
                    args = (rec or {}).get("args") or {}
                    if args.get("account_id") == handled["account_id"] and args.get("folder"):
                        conn.execute("UPDATE handled SET folder=? WHERE rule_id=? AND message_id=? AND account_id=?",
                                     (normalize_folder(args["folder"]), handled["rule_id"], handled["message_id"], handled["account_id"]))
            if "uidvalidity" not in cols:
                conn.execute("ALTER TABLE handled ADD COLUMN uidvalidity TEXT NOT NULL DEFAULT ''")
            info = conn.execute("PRAGMA table_info(handled)").fetchall()
            if sum(bool(r[5]) for r in info) == 2:
                conn.execute("""CREATE TABLE handled_scoped (
                    rule_id TEXT NOT NULL, account_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL, sender TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, reason TEXT, request_id TEXT, ts REAL NOT NULL,
                    feedback TEXT, received_ts REAL, folder TEXT NOT NULL DEFAULT 'INBOX',
                    uidvalidity TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(rule_id, account_id, folder, uidvalidity, message_id))""")
                conn.execute("INSERT INTO handled_scoped SELECT rule_id, account_id, message_id, sender, status, reason, request_id, ts, feedback, received_ts, folder, uidvalidity FROM handled")
                conn.execute("DROP TABLE handled")
                conn.execute("ALTER TABLE handled_scoped RENAME TO handled")
                conn.execute("CREATE INDEX ix_handled_rule_ts ON handled(rule_id, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_handled_message_scope ON handled(account_id, folder, message_id)")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(rules)")}
            if "reply_to_body_address" not in cols:
                conn.execute("ALTER TABLE rules ADD COLUMN"
                             " reply_to_body_address INTEGER NOT NULL"
                             " DEFAULT 0")
            if "cc" not in cols:
                conn.execute("ALTER TABLE rules ADD COLUMN cc TEXT"
                             " NOT NULL DEFAULT '[]'")
            if "attachments" not in cols:
                conn.execute("ALTER TABLE rules ADD COLUMN attachments"
                             " TEXT NOT NULL DEFAULT '[]'")
            # stato piccolo del watcher (es. offset di getUpdates Telegram)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    # ------------------------------------------------------------- rules

    def create(self, *, account_id: int, trigger_kind: str,
               trigger_values: List[str], reply_style: str,
               doc_paths: List[str], mode: str,
               first_contact: str = "semi",
               daily_cap: int = DEFAULT_DAILY_CAP,
               cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
               expiry_days: float = DEFAULT_EXPIRY_DAYS,
               created_by: str = "unknown",
               hello_verified_at: float = 0.0,
               reply_to_body_address: bool = False,
               cc: Optional[List[str]] = None,
               attachments: Optional[List[str]] = None) -> str:
        """Registra una regola. `hello_verified_at` e' l'istante in cui il
        chiamante ha superato consent.require_human: DEVE essere valorizzato
        (il chiamante e' la CLI/console, mai un tool MCP). Qui e' un dato,
        non un controllo: il controllo sta nel fatto che solo codice con
        accesso a questo modulo — e al prompt OS — puo' arrivarci."""
        if trigger_kind not in TRIGGER_KINDS:
            raise ValueError(f"trigger_kind '{trigger_kind}' non valido")
        if mode not in MODES:
            raise ValueError(f"mode '{mode}' non valido")
        if first_contact not in MODES:
            raise ValueError(f"first_contact '{first_contact}' non valido")
        if not trigger_values:
            raise ValueError("trigger_values vuoto")
        if not hello_verified_at:
            raise ValueError(
                "hello_verified_at mancante: una regola nasce solo dietro "
                "verifica dell'utente fisico (Windows Hello / Touch ID)")
        rule_id = "rule_" + secrets.token_hex(4)
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO rules (rule_id, account_id, trigger_kind,"
                " trigger_values, reply_style, doc_paths, mode, first_contact,"
                " daily_cap, cooldown_hours, expires_at, created_at,"
                " created_by, hello_verified_at, reply_to_body_address,"
                " cc, attachments)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rule_id, int(account_id), trigger_kind,
                 json.dumps(trigger_values, ensure_ascii=False),
                 reply_style, json.dumps(doc_paths, ensure_ascii=False),
                 mode, first_contact, int(daily_cap), float(cooldown_hours),
                 now + expiry_days * 86400, now, created_by,
                 float(hello_verified_at), 1 if reply_to_body_address else 0,
                 json.dumps(cc or [], ensure_ascii=False),
                 json.dumps(attachments or [], ensure_ascii=False)),
            )
        return rule_id

    @staticmethod
    def _row_to_rule(row) -> Dict[str, Any]:
        d = dict(row)
        d["trigger_values"] = json.loads(d["trigger_values"])
        d["doc_paths"] = json.loads(d["doc_paths"])
        d["paused"] = bool(d["paused"])
        d["reply_to_body_address"] = bool(d.get("reply_to_body_address", 0))
        d["cc"] = json.loads(d.get("cc") or "[]")
        d["attachments"] = json.loads(d.get("attachments") or "[]")
        d["expired"] = time.time() > d["expires_at"]
        return d

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM rules WHERE rule_id=?", (rule_id,)).fetchone()
        return self._row_to_rule(row) if row else None

    def list_all(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rules ORDER BY created_at DESC").fetchall()
        return [self._row_to_rule(r) for r in rows]

    def badge_summary(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT paused, pause_reason FROM rules WHERE paused=1").fetchall()
        return [{"state": "paused", "pause_reason": row["pause_reason"]} for row in rows]

    def active(self) -> List[Dict[str, Any]]:
        """Regole che il watcher deve considerare: non in pausa, non scadute."""
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rules WHERE paused=0 AND expires_at > ?"
                " ORDER BY created_at", (now,)).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def record_contact(self, rule_id: str, account_id: int, address: str) -> None:
        """The rule answered (or drafted an answer to) this person."""
        address = (address or "").strip().lower()
        if not address or "@" not in address:
            return
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO contacts (rule_id, account_id, address, ts)"
                " VALUES (?,?,?,?)", (rule_id, int(account_id), address, time.time()))

    def is_contact(self, rule_id: str, address: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM contacts WHERE rule_id=? AND address=? LIMIT 1",
                (rule_id, (address or "").strip().lower())).fetchone()
        return row is not None

    def pause(self, rule_id: str, reason: str = "") -> bool:
        """Mettere in pausa e' sempre lecito, da chiunque: riduce l'autonomia.
        E' riattivare che richiede l'umano."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE rules SET paused=1, pause_reason=? WHERE rule_id=?",
                (reason or None, rule_id))
            return cur.rowcount == 1

    def resume(self, rule_id: str, hello_verified_at: float) -> bool:
        """Riattivazione: come la creazione, solo dietro verifica umana."""
        if not hello_verified_at:
            raise ValueError("hello_verified_at mancante")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE rules SET paused=0, pause_reason=NULL,"
                " hello_verified_at=? WHERE rule_id=?",
                (float(hello_verified_at), rule_id))
            return cur.rowcount == 1

    def delete(self, rule_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM rules WHERE rule_id=?", (rule_id,))
            conn.execute("DELETE FROM handled WHERE rule_id=?", (rule_id,))
            return cur.rowcount == 1

    # ----------------------------------------------------------- handled

    # 'matched' e' uno stato di passaggio: viene scritto all'inizio del
    # giro e sostituito appena la bozza c'e'. Se resta, il processo e'
    # morto a meta' — PC spento, watcher riavviato — e la mail non va
    # persa per sempre solo perche' era in mano a qualcuno che non c'e'
    # piu'. Oltre questa soglia si rilavora.
    MATCHED_ORFANO_SECONDI = 1800

    def already_handled(self, rule_id: str, message_id: str, *, account_id=None, folder=None, uidvalidity=None) -> bool:
        scope, values = _scope_sql(account_id, folder, uidvalidity)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status, ts FROM handled WHERE rule_id=? AND message_id=?" + scope,
                [rule_id, str(message_id), *values]).fetchone()
        if row is None:
            return False
        if row["status"] == "matched" and (
                time.time() - float(row["ts"] or 0)
                > self.MATCHED_ORFANO_SECONDI):
            return False
        return True

    def record(self, rule_id: str, account_id: int, message_id: str,
               sender: str, status: str, reason: str = "",
               request_id: str = "", received_ts: Optional[float] = None, *, folder=None, uidvalidity="") -> None:
        if folder is None:
            # Approval payloads preserve folders for built-in rules too.
            from gigamail import policy
            rec = policy.store().get(request_id) if request_id else None
            rule = self.get(rule_id)
            folder = ((rec or {}).get("args") or {}).get("folder")
            if not folder and rule and rule.get("trigger_kind") == "folder":
                folder = rule["trigger_values"][0]
        folder = normalize_folder(folder)
        with self._conn() as conn:
            if received_ts is None:
                row = conn.execute(
                    "SELECT received_ts FROM handled WHERE rule_id=? AND message_id=? AND account_id=? AND folder=? AND uidvalidity=?",
                    (rule_id, str(message_id), account_id, folder, str(uidvalidity))).fetchone()
                received_ts = row["received_ts"] if row else None
            conn.execute(
                "INSERT OR REPLACE INTO handled (rule_id, account_id,"
                " message_id, sender, status, reason, request_id, ts, received_ts, folder, uidvalidity)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rule_id, int(account_id), str(message_id),
                 (sender or "").lower(), status, reason or None,
                 request_id or None, time.time(), received_ts, folder, str(uidvalidity)))

    def set_status(self, rule_id: str, message_id: str, status: str,
                   reason: str = "", *, account_id=None, folder=None, uidvalidity=None) -> None:
        scope, values = _scope_sql(account_id, folder, uidvalidity)
        with self._conn() as conn:
            conn.execute(
                "UPDATE handled SET status=?, reason=?, ts=?"
                " WHERE rule_id=? AND message_id=?" + scope,
                [status, reason or None, time.time(), rule_id, str(message_id), *values])

    def get_handled(self, rule_id: str, message_id: str, *, account_id=None, folder=None, uidvalidity=None) -> Optional[Dict[str, Any]]:
        scope, values = _scope_sql(account_id, folder, uidvalidity)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM handled WHERE rule_id=? AND message_id=?" + scope,
                [rule_id, str(message_id), *values]).fetchone()
        return dict(row) if row else None

    def find_by_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """La riga handled che ha generato una certa richiesta di
        approvazione: serve per tradurre un "approva req_x" arrivato da
        Telegram nella mail e nella regola a cui appartiene."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM handled WHERE request_id=?",
                (request_id,)).fetchone()
        return dict(row) if row else None

    def replies_to(self, message_id: str, *, account_id=None, folder=None, uidvalidity=None) -> List[Dict[str, Any]]:
        """Every handled row for one mail, whatever drafted the reply: a
        rule, a reply asked from Telegram, the video-call link. One mail
        deserves one reply, and each path checks here before drafting
        (core.reply_guard)."""
        scope, values = _scope_sql(account_id, folder, uidvalidity)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM handled WHERE message_id=?" + scope + " ORDER BY ts",
                [str(message_id), *values]).fetchall()
        return [dict(r) for r in rows]

    def request_retry(self, rule_id: str, message_id: str, feedback: str, *, account_id=None, folder=None, uidvalidity=None) -> None:
        """L'umano ha rifiutato la bozza e chiesto una modifica: la mail
        torna in coda con il feedback, e il watcher la rifa' al prossimo
        giro (process_retries) — ripassando dal gate."""
        scope, values = _scope_sql(account_id, folder, uidvalidity)
        with self._conn() as conn:
            conn.execute(
                "UPDATE handled SET status='retry', reason=NULL, feedback=?,"
                " ts=? WHERE rule_id=? AND message_id=?" + scope,
                [feedback, time.time(), rule_id, str(message_id), *values])

    def retries(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM handled WHERE status='retry'").fetchall()
        return [dict(r) for r in rows]

    def kv_get(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def kv_set(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)",
                         (key, str(value)))

    def kv_set_many(self, values: Dict[str, str]) -> None:
        """Several keys in one transaction: one commit, not one per key."""
        with self._conn() as conn:
            conn.executemany("INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)",
                             [(k, str(v)) for k, v in values.items()])

    def kv_keys(self, prefix: str) -> List[str]:
        """Keys starting with `prefix`, in key order."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._conn() as conn:
            rows = conn.execute("SELECT key FROM kv WHERE key LIKE ? ESCAPE '\\'"
                                " ORDER BY key", (like + "%",)).fetchall()
        return [r["key"] for r in rows]

    def kv_delete(self, key: str) -> bool:
        """Remove a key; True when this call removed it, so two readers of a
        queue never both take the same entry."""
        with self._conn() as conn:
            return conn.execute("DELETE FROM kv WHERE key=?", (key,)).rowcount > 0

    def pending_requests(self, rule_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Le mail per cui esiste una richiesta di approvazione creata dal
        watcher e non ancora conclusa (semi in attesa dell'umano)."""
        q = ("SELECT * FROM handled WHERE status='awaiting_approval'"
             " AND request_id IS NOT NULL")
        args: tuple = ()
        if rule_id:
            q += " AND rule_id=?"
            args = (rule_id,)
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # Contatori per le barriere che hanno bisogno di memoria: il tetto
    # giornaliero conta le risposte USCITE, il cooldown guarda l'ultima
    # risposta allo stesso mittente, la raffica conta i MATCH (qualunque
    # esito: e' il volume in ingresso che segnala l'attacco).

    def sent_today(self, rule_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM handled WHERE rule_id=?"
                " AND status='sent' AND ts > ?",
                (rule_id, time.time() - 86400)).fetchone()
        return int(row["n"]) if row else 0

    def last_reply_to(self, rule_id: str, sender: str) -> Optional[float]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(ts) AS t FROM handled WHERE rule_id=? AND sender=?"
                " AND status IN ('sent','awaiting_approval')",
                (rule_id, (sender or "").lower())).fetchone()
        return float(row["t"]) if row and row["t"] else None

    def matches_since(self, rule_id: str, since_ts: float) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM handled WHERE rule_id=? AND ts > ?",
                (rule_id, since_ts)).fetchone()
        return int(row["n"]) if row else 0

    def matches_received_between(self, rule_id: str, lo: float, hi: float) -> int:
        """Mails of this rule that ARRIVED in [lo, hi]: the burst window
        by arrival, so six mails spread over a night are six mails, not
        a burst, however many the watcher reads in one tick."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM handled WHERE rule_id=?"
                " AND received_ts IS NOT NULL AND received_ts >= ? AND received_ts <= ?",
                (rule_id, lo, hi)).fetchone()
        return int(row["n"]) if row else 0

    def ever_replied_to(self, sender: str, account_id: int) -> bool:
        """Il mittente ha gia' ricevuto una risposta approvata (da qualunque
        regola di questo account)? Serve a first_contact: il primo messaggio
        di un mittente nuovo passa sempre dall'umano; dai successivi in poi
        la fiducia e' progressiva."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM handled WHERE account_id=? AND sender=?"
                " AND status='sent' LIMIT 1",
                (int(account_id), (sender or "").lower())).fetchone()
        return row is not None


_store: Optional[RuleStore] = None


def store() -> RuleStore:
    global _store
    if _store is None:
        _store = RuleStore()
    return _store


def set_store(new_store: RuleStore) -> None:
    """Usata dai test per isolare il database."""
    global _store
    _store = new_store
