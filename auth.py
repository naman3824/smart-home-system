"""
Authentication — password hashing and session tokens.

Uses only Python's standard library (hashlib, hmac, secrets) so there's no
extra pip dependency (no passlib/bcrypt install needed on the AWS deploy).

Password hashing: PBKDF2-HMAC-SHA256 with a random per-user salt and 200,000
iterations — this is the same algorithm Django uses by default and is
considered adequate for this kind of application (a household dashboard,
not a bank). It's available directly via hashlib.pbkdf2_hmac, no extra
library required.

Sessions: a random 32-byte token (via secrets.token_urlsafe) stored server
side in the sessions table, set as an HttpOnly cookie. The token itself
carries no information — it's just a lookup key — so there's nothing to
forge even if someone reads the cookie value.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

import db

PBKDF2_ITERATIONS = 200_000
SESSION_DURATION_HOURS = 24 * 7  # sessions last a week before re-login is required


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Returns (password_hash_hex, salt_hex). Generates a new salt if not provided."""
    if salt is None:
        salt = secrets.token_hex(16)
    salt_bytes = bytes.fromhex(salt)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Constant-time comparison to avoid timing attacks."""
    test_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(test_hash, password_hash)


def create_user_account(username: str, password: str, display_name: str, role="member", member_id=None):
    password_hash, salt = hash_password(password)
    return db.create_user(
        username=username,
        password_hash=password_hash,
        password_salt=salt,
        display_name=display_name,
        role=role,
        member_id=member_id,
    )


def authenticate(username: str, password: str):
    """Returns the user dict on success, None on failure. Does not create a session."""
    user = db.get_user_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"], user["password_salt"]):
        return None
    return user


def start_session(user_id: int) -> str:
    """Creates a new session and returns the token to set as a cookie."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(hours=SESSION_DURATION_HOURS)).isoformat()
    db.create_session(token, user_id, expires_at)
    db.update_last_login(user_id)
    return token


def get_session_user(token: str):
    """Returns the user dict for a valid, non-expired session token, else None."""
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    expires_at = datetime.fromisoformat(session["expires_at"])
    if expires_at < datetime.utcnow():
        db.delete_session(token)
        return None
    return session


def end_session(token: str):
    if token:
        db.delete_session(token)
