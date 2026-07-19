"""
Persistence layer — SQLite (Python stdlib only, no extra dependency).

Why this exists: the dashboard runs in a Docker container on AWS that gets
rebuilt and redeployed on every push. Without a database, every redeploy
wiped all security logs, family member changes, and anything else held only
in memory — meaning the "live" site never actually remembered anything.

The database file lives at DB_PATH (default ./data/smarthome.db). In
production this path should be a mounted Docker volume so the file survives
container restarts/redeploys — see Dockerfile / deploy notes.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "smarthome.db"))

# The house this dashboard runs for is fixed in Gurugram, Haryana — always
# IST (UTC+5:30), no DST. The container itself runs on UTC system time (the
# default for python:3.11-slim on AWS unless a TZ is explicitly set), so
# every "now" used for a timestamp that gets stored or shown to a person
# needs to go through this helper — otherwise audit logs, "last on since",
# routine fire times etc. all read ~5.5 hours behind real local time.
# Purely-internal duration math (e.g. "seconds since last tick") doesn't
# need this — it's fine in any consistent timezone since it's a difference.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Current time as a naive datetime already shifted to IST — safe to
    strftime/compare against strings stored via now_ist_str()/SQL defaults
    below, which all use the same +5:30 offset."""
    return datetime.now(timezone.utc).astimezone(IST).replace(tzinfo=None)


def now_ist_str() -> str:
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


# SQLite's own `datetime('now', '+5 hours', '+30 minutes')` is always UTC with no server-side timezone
# setting — this modifier string shifts it to IST at the SQL level, for use
# in CREATE TABLE ... DEFAULT clauses and other in-SQL "now" references.
SQL_NOW_IST = "datetime('now', '+5 hours', '+30 minutes')"

# SQLite connections aren't thread-safe by default; the dashboard reads/writes
# from the sensor simulation thread AND the FastAPI request threads, so every
# write takes this lock. Reads are fast enough not to need a connection pool.
_db_lock = threading.Lock()


def _ensure_data_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    """Context manager yielding a SQLite connection with row access by column name."""
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't already exist. Safe to call on every startup."""
    with _db_lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS security_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                person      TEXT NOT NULL,
                type        TEXT NOT NULL,
                event       TEXT NOT NULL,
                time        TEXT NOT NULL,
                date        TEXT NOT NULL,
                status      TEXT NOT NULL,
                estimated   TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS family_members (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                name    TEXT NOT NULL UNIQUE,
                role    TEXT NOT NULL DEFAULT 'Member',
                status  TEXT NOT NULL DEFAULT 'away',
                avatar  TEXT NOT NULL,
                color   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS device_state (
                room        TEXT NOT NULL,
                device      TEXT NOT NULL,
                state_json  TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                PRIMARY KEY (room, device)
            );

            CREATE TABLE IF NOT EXISTS kv_store (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT NOT NULL UNIQUE,
                password_hash   TEXT NOT NULL,
                password_salt   TEXT NOT NULL,
                display_name    TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'member',
                member_id       INTEGER,
                created_at      TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                last_login_at   TEXT,
                FOREIGN KEY (member_id) REFERENCES family_members(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                expires_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL,
                action      TEXT NOT NULL,
                detail      TEXT,
                ip_address  TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS automation_rules (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT NOT NULL,
                description        TEXT,
                condition_json     TEXT NOT NULL,
                action_json        TEXT NOT NULL,
                enabled            INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds   INTEGER NOT NULL DEFAULT 300,
                created_at         TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS automation_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id     INTEGER,
                rule_name   TEXT NOT NULL,
                detail      TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS routines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id   INTEGER NOT NULL,
                member_name TEXT NOT NULL,
                name        TEXT NOT NULL,
                hour        INTEGER NOT NULL,
                minute      INTEGER NOT NULL DEFAULT 0,
                days        TEXT NOT NULL DEFAULT 'everyday',
                room        TEXT NOT NULL,
                device      TEXT NOT NULL,
                action_json TEXT NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS scheduled_guests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT 'guest',
                days        TEXT NOT NULL DEFAULT 'everyday',
                start_hour  INTEGER NOT NULL DEFAULT 0,
                start_min   INTEGER NOT NULL DEFAULT 0,
                end_hour    INTEGER NOT NULL DEFAULT 23,
                end_min     INTEGER NOT NULL DEFAULT 59,
                enabled     INTEGER NOT NULL DEFAULT 1,
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS rent_config (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                total_rent      REAL NOT NULL DEFAULT 0,
                due_day         INTEGER NOT NULL DEFAULT 1,
                auto_pay        INTEGER NOT NULL DEFAULT 0,
                notes           TEXT,
                updated_at      TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            CREATE TABLE IF NOT EXISTS payments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id       INTEGER NOT NULL,
                member_name     TEXT NOT NULL,
                amount          REAL NOT NULL,
                month           TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                paid_at         TEXT,
                payment_method  TEXT,
                notes           TEXT,
                recorded_by     TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );

            -- One row per calendar date, kwh is a running total for that date
            -- (overwritten throughout the day as usage accrues, not appended).
            -- Powers the Energy tab's "this week vs last week" insight — see
            -- server.py's simulate_sensors() tick loop for the writer side.
            CREATE TABLE IF NOT EXISTS energy_daily (
                date        TEXT PRIMARY KEY,
                kwh         REAL NOT NULL DEFAULT 0,
                cost        REAL NOT NULL DEFAULT 0,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );
            """
        )


# ── Security logs ──────────────────────────────────────────────────────

def get_security_logs(limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM security_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_security_log(person, type_, event, time_str, date_str, status, estimated=None):
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO security_logs (person, type, event, time, date, status, estimated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (person, type_, event, time_str, date_str, status, estimated),
        )
        row = conn.execute(
            "SELECT * FROM security_logs WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


# ── Family members ─────────────────────────────────────────────────────

def get_family_members():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM family_members ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def seed_family_if_empty(defaults):
    """Insert default family members only if the table is empty (first run ever)."""
    with _db_lock, get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM family_members").fetchone()["c"]
        if count > 0:
            return
        for m in defaults:
            conn.execute(
                """INSERT INTO family_members (id, name, role, status, avatar, color)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (m["id"], m["name"], m["role"], m["status"], m["avatar"], m["color"]),
            )


def add_family_member(name, role, status, avatar, color):
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO family_members (name, role, status, avatar, color)
               VALUES (?, ?, ?, ?, ?)""",
            (name, role, status, avatar, color),
        )
        row = conn.execute(
            "SELECT * FROM family_members WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def delete_family_member(member_id):
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM family_members WHERE id = ?", (member_id,))


def update_member_status(name, status):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE family_members SET status = ? WHERE LOWER(name) = LOWER(?)",
            (status, name),
        )


# ── Device state ───────────────────────────────────────────────────────

def save_device_state(room, device, state_dict):
    import json
    with _db_lock, get_conn() as conn:
        conn.execute(
            """INSERT INTO device_state (room, device, state_json, updated_at)
               VALUES (?, ?, ?, datetime('now', '+5 hours', '+30 minutes'))
               ON CONFLICT(room, device) DO UPDATE SET
                 state_json = excluded.state_json,
                 updated_at = excluded.updated_at""",
            (room, device, json.dumps(state_dict)),
        )


def load_all_device_state():
    """Returns {room: {device: state_dict}} for everything persisted, or {} if none yet."""
    import json
    with get_conn() as conn:
        rows = conn.execute("SELECT room, device, state_json FROM device_state").fetchall()
    result = {}
    for r in rows:
        result.setdefault(r["room"], {})[r["device"]] = json.loads(r["state_json"])
    return result


# ── Generic key/value store (small bits of persisted state) ───────────

def kv_get(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def kv_set(key, value):
    with _db_lock, get_conn() as conn:
        conn.execute(
            """INSERT INTO kv_store (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


# ── Users (authentication) ─────────────────────────────────────────────

def create_user(username, password_hash, password_salt, display_name, role="member", member_id=None):
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, password_salt, display_name, role, member_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (username.lower(), password_hash, password_salt, display_name, role, member_id),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_user_by_username(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.lower(),)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, role, member_id, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def update_last_login(user_id):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = datetime('now', '+5 hours', '+30 minutes') WHERE id = ?", (user_id,)
        )


def count_users():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


# ── Sessions ────────────────────────────────────────────────────────────

def create_session(token, user_id, expires_at_iso):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at_iso),
        )


def get_session(token):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT sessions.*, users.username, users.display_name, users.role, users.member_id
               FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token = ?""",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token):
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_expired_sessions():
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now', '+5 hours', '+30 minutes')")


# ── Audit log ───────────────────────────────────────────────────────────

def update_user_password(user_id, new_hash, new_salt):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (new_hash, new_salt, user_id),
        )


def add_audit_entry(username, action, detail=None, ip_address=None):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (username, action, detail, ip_address) VALUES (?, ?, ?, ?)",
            (username, action, detail, ip_address),
        )


def get_audit_log(limit: int = 300):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Automation rules ─────────────────────────────────────────────────────

def _row_to_rule(row):
    import json
    d = dict(row)
    d["condition"] = json.loads(d.pop("condition_json"))
    d["action"] = json.loads(d.pop("action_json"))
    d["enabled"] = bool(d["enabled"])
    return d


def get_automation_rules(enabled_only=False):
    with get_conn() as conn:
        if enabled_only:
            rows = conn.execute("SELECT * FROM automation_rules WHERE enabled = 1 ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM automation_rules ORDER BY id").fetchall()
        return [_row_to_rule(r) for r in rows]


def get_automation_rule(rule_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM automation_rules WHERE id = ?", (rule_id,)).fetchone()
        return _row_to_rule(row) if row else None


def create_automation_rule(name, description, condition, action, enabled=True, cooldown_seconds=300):
    import json
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO automation_rules (name, description, condition_json, action_json, enabled, cooldown_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, description, json.dumps(condition), json.dumps(action), 1 if enabled else 0, cooldown_seconds),
        )
        row = conn.execute("SELECT * FROM automation_rules WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_rule(row)


def update_automation_rule_enabled(rule_id, enabled):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE automation_rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id)
        )


def update_automation_rule(rule_id, name, description, condition, action, cooldown_seconds):
    """Full edit of an existing rule's definition (enabled state is left
    alone — use update_automation_rule_enabled for that toggle)."""
    import json
    with _db_lock, get_conn() as conn:
        conn.execute(
            """UPDATE automation_rules
               SET name=?, description=?, condition_json=?, action_json=?, cooldown_seconds=?
               WHERE id=?""",
            (name, description, json.dumps(condition), json.dumps(action), cooldown_seconds, rule_id),
        )
        row = conn.execute("SELECT * FROM automation_rules WHERE id = ?", (rule_id,)).fetchone()
        return _row_to_rule(row) if row else None


def delete_automation_rule(rule_id):
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM automation_rules WHERE id = ?", (rule_id,))


def seed_automation_rules_if_empty(defaults):
    """Insert starter rules only if the table is empty (first run ever)."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM automation_rules").fetchone()["c"]
    if count > 0:
        return
    for r in defaults:
        create_automation_rule(
            name=r["name"], description=r.get("description"),
            condition=r["condition"], action=r["action"],
            enabled=r.get("enabled", True), cooldown_seconds=r.get("cooldown_seconds", 300),
        )


def add_automation_run(rule_id, rule_name, detail):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO automation_runs (rule_id, rule_name, detail) VALUES (?, ?, ?)",
            (rule_id, rule_name, detail),
        )


def get_automation_runs(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM automation_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_automation_run_stats():
    """Per-rule fire counts (today + all-time), plus the overall total fired
    today — powers the Automation page's stats header and per-rule badges.
    Cheap enough to compute on every status poll: automation_runs is small
    relative to a household's actual usage volume."""
    today_str = now_ist_str()[:10]
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT rule_id, created_at FROM automation_runs WHERE rule_id IS NOT NULL"
        ).fetchall()
    per_rule = {}
    total_today = 0
    for r in rows:
        rid = r["rule_id"]
        entry = per_rule.setdefault(rid, {"total": 0, "today": 0})
        entry["total"] += 1
        if r["created_at"][:10] == today_str:
            entry["today"] += 1
            total_today += 1
    return {"per_rule": per_rule, "total_today": total_today, "total_all_time": len(rows)}


# ── Routines ─────────────────────────────────────────────────────────────

def _row_to_routine(row):
    import json
    d = dict(row)
    d["action"] = json.loads(d.pop("action_json"))
    d["enabled"] = bool(d["enabled"])
    return d


def get_routines(member_id=None):
    with get_conn() as conn:
        if member_id is not None:
            rows = conn.execute(
                "SELECT * FROM routines WHERE member_id = ? ORDER BY hour, minute", (member_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM routines ORDER BY member_id, hour, minute"
            ).fetchall()
        return [_row_to_routine(r) for r in rows]


def get_routine(routine_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        return _row_to_routine(row) if row else None


def create_routine(member_id, member_name, name, hour, minute, days, room, device, action):
    import json
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO routines
               (member_id, member_name, name, hour, minute, days, room, device, action_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (member_id, member_name, name, hour, minute, days, room, device, json.dumps(action)),
        )
        row = conn.execute("SELECT * FROM routines WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_routine(row)


def update_routine_enabled(routine_id, enabled):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE routines SET enabled = ? WHERE id = ?", (1 if enabled else 0, routine_id)
        )


def delete_routine(routine_id):
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM routines WHERE id = ?", (routine_id,))


def get_enabled_routines_for_tick():
    """Returns all enabled routines — called every sensor tick."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM routines WHERE enabled = 1"
        ).fetchall()
        return [_row_to_routine(r) for r in rows]


# ── Scheduled guests ──────────────────────────────────────────────────────

def get_scheduled_guests(enabled_only=False):
    with get_conn() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM scheduled_guests WHERE enabled = 1 ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_guests ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]


def get_scheduled_guest_by_name(name):
    """Case-insensitive lookup — used when a face is detected."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_guests WHERE LOWER(name) = LOWER(?) AND enabled = 1",
            (name,)
        ).fetchone()
        return dict(row) if row else None


def create_scheduled_guest(name, role, days, start_hour, start_min, end_hour, end_min, notes=None):
    with _db_lock, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO scheduled_guests
               (name, role, days, start_hour, start_min, end_hour, end_min, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, role, days, start_hour, start_min, end_hour, end_min, notes),
        )
        row = conn.execute(
            "SELECT * FROM scheduled_guests WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def update_scheduled_guest_enabled(guest_id, enabled):
    with _db_lock, get_conn() as conn:
        conn.execute(
            "UPDATE scheduled_guests SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, guest_id)
        )


def delete_scheduled_guest(guest_id):
    with _db_lock, get_conn() as conn:
        conn.execute("DELETE FROM scheduled_guests WHERE id = ?", (guest_id,))


def get_scheduled_guest(guest_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_guests WHERE id = ?", (guest_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Rent config + payments ────────────────────────────────────────────────

def get_rent_config():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM rent_config ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else {"id": None, "total_rent": 0, "due_day": 1, "auto_pay": 0, "notes": ""}


def upsert_rent_config(total_rent, due_day, auto_pay=False, notes=None):
    from datetime import datetime as _dt
    with _db_lock, get_conn() as conn:
        existing = conn.execute("SELECT id FROM rent_config LIMIT 1").fetchone()
        if existing:
            conn.execute(
                """UPDATE rent_config SET total_rent=?, due_day=?, auto_pay=?, notes=?, updated_at=datetime('now', '+5 hours', '+30 minutes')
                   WHERE id=?""",
                (total_rent, due_day, 1 if auto_pay else 0, notes, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO rent_config (total_rent, due_day, auto_pay, notes) VALUES (?, ?, ?, ?)",
                (total_rent, due_day, 1 if auto_pay else 0, notes)
            )
        
        # When rent changes, update any pending payments for the current month to the new share
        month = _dt.now().strftime("%Y-%m")
        # Currently 6 members hardcoded or fetch from family_members logic.
        # It's safest to just update where status='pending' and month=month
        n = 6 # len(family_members)
        per_share = round(total_rent / n, 2) if n > 0 and total_rent > 0 else 0
        conn.execute(
            "UPDATE payments SET amount = ? WHERE month = ? AND status = 'pending'",
            (per_share, month)
        )
    return get_rent_config()


def get_payments(month=None, member_id=None):
    with get_conn() as conn:
        query = "SELECT * FROM payments WHERE 1=1"
        params = []
        if month:
            query += " AND month = ?"
            params.append(month)
        if member_id is not None:
            query += " AND member_id = ?"
            params.append(member_id)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_or_create_monthly_payments(month, members, per_share):
    """Ensure every member has a payment record for the given month."""
    with _db_lock, get_conn() as conn:
        for m in members:
            existing = conn.execute(
                "SELECT id FROM payments WHERE member_id=? AND month=?", (m["id"], month)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO payments (member_id, member_name, amount, month, status) VALUES (?,?,?,?,?)",
                    (m["id"], m["name"], per_share, month, "pending")
                )
    return get_payments(month=month)


def mark_payment(payment_id, status, payment_method=None, notes=None, recorded_by=None):
    from datetime import datetime as _dt
    paid_at = _dt.now().isoformat() if status == "paid" else None
    with _db_lock, get_conn() as conn:
        conn.execute(
            """UPDATE payments SET status=?, paid_at=?, payment_method=?, notes=?, recorded_by=?
               WHERE id=?""",
            (status, paid_at, payment_method, notes, recorded_by, payment_id)
        )
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        return dict(row) if row else None


def get_payment(payment_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        return dict(row) if row else None


# ── Energy daily log (for week-over-week insights) ─────────────────────────

def upsert_energy_daily(date_str, kwh, cost):
    """Overwrites today's running total — called periodically from the sensor
    tick loop, not appended per-sample, so this stays a cheap upsert."""
    with _db_lock, get_conn() as conn:
        conn.execute(
            """INSERT INTO energy_daily (date, kwh, cost, updated_at) VALUES (?, ?, ?, datetime('now', '+5 hours', '+30 minutes'))
               ON CONFLICT(date) DO UPDATE SET kwh=excluded.kwh, cost=excluded.cost, updated_at=excluded.updated_at""",
            (date_str, kwh, cost),
        )


def get_energy_daily_range(days: int = 21):
    """Last `days` calendar dates of energy_daily, oldest first. Only returns
    rows that actually exist (no zero-filling) — callers decide how to handle
    gaps/missing history."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM energy_daily ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
