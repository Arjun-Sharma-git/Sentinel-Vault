"""Passwords and server-side sessions.

- Passwords: Argon2id with OWASP's minimum parameters (19 MiB memory, 2 iterations, 1 lane).
- Sessions: the browser holds a random token in an HttpOnly, SameSite=Strict cookie; the
  database keeps only its SHA-256. Sessions expire after `session_hours`, or after
  `session_idle_minutes` without a request, and signing out deletes the row, so revocation
  is immediate (unlike a stateless JWT).
- CSRF: state-changing requests must carry the `X-Vault-Request` header. Browsers can't add
  custom headers cross-site without a CORS preflight, which the API refuses for other origins.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import Session, User, utcnow

COOKIE_NAME = "vault_session"
CSRF_HEADER = "X-Vault-Request"

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
# Verified against when the email is unknown, so timing doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        verify_password(_DUMMY_HASH, password)  # equalize timing
        return None
    return user if verify_password(user.password_hash, password) else None


async def create_session(db: AsyncSession, user: User, settings: Settings) -> str:
    """Start a session and return the token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(
        Session(
            token_hash=_token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_hours),
        )
    )
    await db.commit()
    return token


async def resolve_session(db: AsyncSession, token: str, settings: Settings) -> User | None:
    """The active user behind a session token, refreshing its idle timer; None if invalid."""
    if not token:
        return None
    session = await db.get(Session, _token_hash(token))
    now = utcnow()
    if session is None:
        return None
    idle_limit = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if now >= session.expires_at or now >= idle_limit:
        await db.delete(session)
        await db.commit()
        return None
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    session.last_seen_at = now
    await db.commit()
    return user


async def end_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(Session).where(Session.token_hash == _token_hash(token)))
    await db.commit()


async def end_all_sessions(db: AsyncSession, user_id: str) -> None:
    """Sign a user out everywhere (password change, deactivation)."""
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()


def same_token(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
