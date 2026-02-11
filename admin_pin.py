"""Admin PIN authentication for AutoSync settings protection."""

import hashlib
import os
import secrets
import time

from config import _load_user_config, save_user_config

# In-memory session tokens: {token: expiry_timestamp}
_sessions = {}

# Session lifetime in seconds (30 minutes)
SESSION_LIFETIME = 30 * 60


def is_pin_set():
    """Check if an admin PIN has been configured."""
    cfg = _load_user_config()
    return bool(cfg.get("pin_hash"))


def set_pin(pin):
    """Hash and store a new admin PIN."""
    salt = secrets.token_hex(16)
    pin_hash = hashlib.sha256((salt + pin).encode()).hexdigest()
    save_user_config({"pin_hash": pin_hash, "pin_salt": salt})


def verify_pin(pin):
    """Verify a PIN against the stored hash. Returns True if correct."""
    cfg = _load_user_config()
    stored_hash = cfg.get("pin_hash")
    salt = cfg.get("pin_salt")
    if not stored_hash or not salt:
        return False
    candidate = hashlib.sha256((salt + pin).encode()).hexdigest()
    return secrets.compare_digest(candidate, stored_hash)


def generate_session_token():
    """Create a new session token valid for SESSION_LIFETIME seconds."""
    _cleanup_expired()
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_LIFETIME
    return token


def validate_session(token):
    """Check if a session token is valid and not expired."""
    if not token:
        return False
    _cleanup_expired()
    expiry = _sessions.get(token)
    if expiry is None:
        return False
    return time.time() < expiry


def clear_session(token):
    """Invalidate a session token (lock)."""
    _sessions.pop(token, None)


def _cleanup_expired():
    """Remove expired tokens from memory."""
    now = time.time()
    expired = [t for t, exp in _sessions.items() if now >= exp]
    for t in expired:
        del _sessions[t]
