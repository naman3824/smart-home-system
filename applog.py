"""
Application/system logging — for debugging the live deployment.

This is distinct from:
  - security_logs (db.py)  — who physically arrived/left the house
  - audit_log (db.py)      — who changed what in the dashboard (logins, toggles)

This module is for the third kind of "logs": server errors, crashes,
unhandled exceptions, and startup/shutdown events — the things you'd need
to debug "why did the live site break at 2am" without SSH-ing in and
guessing from scattered print() statements.

Uses only Python's standard library (logging, logging.handlers) — no new
pip dependency. Writes to:
  - stdout                          (so `docker logs` / AWS CloudWatch still
                                      show everything exactly as before)
  - data/logs/server.log            (rotating file, survives container
                                      restarts if /app/data is volume-mounted,
                                      same volume already used for the database)

The log directory is INSIDE the same /app/data volume as the SQLite database,
so no extra Docker volume configuration is needed — it rides along with the
persistence setup from Step 1.
"""

import logging
import logging.handlers
import os

LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.path.dirname(__file__), "data", "logs"))
LOG_FILE = os.path.join(LOG_DIR, "server.log")

# Rotate at 5MB, keep 5 old files — caps total disk usage at ~30MB even if
# the server runs for months without anyone clearing it out.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def setup_logging():
    """Call once at server startup. Returns the configured logger."""
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("smarthome")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if setup_logging() is somehow called twice
    # (e.g. with --reload during local development)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — stdout, same as before, so docker logs / CloudWatch
    # behave exactly as they did with the old print() statements.
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Rotating file handler — survives container restarts via the volume
    # mount, lets you `cat`/`tail` history even after a redeploy.
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # If the volume isn't writable for some reason, don't crash the
        # whole server over logging — just fall back to console-only.
        logger.warning("Could not open log file at %s — logging to console only", LOG_FILE)

    return logger


# Import this directly elsewhere: `from applog import logger`
logger = setup_logging()


def get_recent_logs(lines: int = 200, level: str = None):
    """
    Reads the last N lines from the log file for the /api/system-logs endpoint.
    Optional level filter (e.g. "ERROR") matches on the formatted level field.
    """
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return []

    recent = all_lines[-lines:]
    if level:
        recent = [ln for ln in recent if f"| {level.upper():<8}|" in ln or f"| {level.upper()} " in ln]
    return [ln.rstrip("\n") for ln in recent]
