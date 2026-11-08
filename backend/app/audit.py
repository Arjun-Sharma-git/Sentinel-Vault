"""Append-only audit trail. Prompts are stored here (access-controlled), never in process logs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent

MAX_TEXT = 1000


async def record(
    db: AsyncSession, actor_id: str | None, action: str, target: str = "", outcome: str = "ok", **detail: Any
) -> None:
    clipped = {k: (v[:MAX_TEXT] if isinstance(v, str) else v) for k, v in detail.items()}
    db.add(AuditEvent(actor_id=actor_id, action=action, target=target[:300], outcome=outcome, detail=clipped))
    await db.commit()
