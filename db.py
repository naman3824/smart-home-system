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

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "smarthome.db"))

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
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
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
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (room, device)
            );

            CREATE TABLE IF NOT EXISTS kv_store (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
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
               VALUES (?, ?, ?, datetime('now'))
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
