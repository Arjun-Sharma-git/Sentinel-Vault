"""Sign-in throttling: lock an account, or a client address, after repeated failed sign-ins.

Counters live in Postgres (`login_throttle`), so every API instance shares them and they
survive restarts. Two keys are counted for each failed attempt:

  email:<address>  stops guessing one account's password (OWASP: account lockout)
  ip:<address>     slows password spraying, one guess each across many accounts

While a key is locked, attempts are refused with 429 *before* the password is checked, so a
locked account can't be used to keep guessing. Unknown emails are counted the same way as
real ones, so lockouts don't reveal which accounts exist. A successful sign-in clears the
account's counter (not the address's).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import LoginThrottle, utcnow


def client_address(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:100]
    return request.client.host if request.client else "unknown"


def login_keys(email: str, address: str, settings: Settings) -> dict[str, int]:
    """Throttle keys for one attempt, each with its failure limit."""
    return {
        f"email:{email.strip().lower()[:254]}": settings.login_max_failures,
        f"ip:{address}": settings.login_ip_max_failures,
    }


async def retry_after(db: AsyncSession, keys: list[str], now: datetime | None = None) -> int:
    """Seconds until every key is unlocked; 0 if none is locked."""
    now = now or utcnow()
    until = await db.scalar(
        select(LoginThrottle.locked_until)
        .where(LoginThrottle.key.in_(keys), LoginThrottle.locked_until > now)
        .order_by(LoginThrottle.locked_until.desc())
        .limit(1)
    )
    return math.ceil((until - now).total_seconds()) if until else 0


async def record_failure(
    db: AsyncSession, key: str, limit: int, settings: Settings, now: datetime | None = None
) -> bool:
    """Count a failed attempt for `key`; lock it when the limit is reached. True if now locked."""
    now = now or utcnow()
    window_expired = LoginThrottle.window_started_at < now - timedelta(minutes=settings.login_window_minutes)
    # One atomic upsert, so concurrent attempts can't race past the limit.
    failures = await db.scalar(
        insert(LoginThrottle)
        .values(key=key, failures=1, window_started_at=now, locked_until=None)
        .on_conflict_do_update(
            index_elements=[LoginThrottle.key],
            set_={
                "failures": case((window_expired, 1), else_=LoginThrottle.failures + 1),
                "window_started_at": case((window_expired, now), else_=LoginThrottle.window_started_at),
            },
        )
        .returning(LoginThrottle.failures)
    )
    locked = failures is not None and failures >= limit
    if locked:
        await db.execute(
            update(LoginThrottle)
            .where(LoginThrottle.key == key)
            .values(
                locked_until=now + timedelta(minutes=settings.login_lockout_minutes),
                failures=0,
                window_started_at=now,
            )
        )
    await db.commit()
    return locked


async def clear(db: AsyncSession, key: str) -> None:
    await db.execute(delete(LoginThrottle).where(LoginThrottle.key == key))
    await db.commit()
