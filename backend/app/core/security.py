"""Security primitives: password hashing, session tokens, CSRF helper."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuthSession, User, utcnow

# --- Password hashing (PBKDF2-HMAC-SHA256, stdlib only — no plain text storage) ---

_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


# --- Session tokens (opaque random token, stored hashed server-side) ---


def _hash_token(token: str) -> str:
    return hashlib.sha256(f"{settings.secret_key}:{token}".encode("utf-8")).hexdigest()


def create_session(db: Session, user: User, user_agent: Optional[str] = None) -> str:
    """Create a session row and return the raw token for the cookie."""
    token = secrets.token_urlsafe(48)
    expires = utcnow() + dt.timedelta(hours=settings.session_expire_hours)
    db.add(AuthSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=expires,
        user_agent=(user_agent or "")[:250] or None,
    ))
    db.commit()
    return token


def resolve_session(db: Session, token: str) -> Optional[User]:
    """Return the user for a valid, unexpired session token (or None)."""
    if not token:
        return None
    row = db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).first()
    if not row:
        return None
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        return None
    return user


def destroy_session(db: Session, token: str) -> None:
    row = db.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).first()
    if row:
        db.delete(row)
        db.commit()


def destroy_all_sessions(db: Session, user_id: int) -> None:
    db.query(AuthSession).filter(AuthSession.user_id == user_id).delete()
    db.commit()


# --- CSRF -------------------------------------------------------------------
# Cookie is SameSite=Lax + HttpOnly. As defence in depth every state-changing
# request must also carry the custom header X-Requested-With, which cross-origin
# forms and simple fetches cannot set without a CORS preflight we control.

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "samadhansetu"


def csrf_ok(header_value: Optional[str]) -> bool:
    return (header_value or "").lower() == CSRF_VALUE
