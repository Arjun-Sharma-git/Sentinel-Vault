"""Ask a question; the answer streams back as Server-Sent Events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..db import session_factory
from ..pipeline import answer_question
from ..sources import SourceError, load_sources, source_folder_ids
from .deps import CurrentUser, Svc, require_csrf_header
from .schemas import AskRequest

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_csrf_header)])


class AskSlots:
    """Questions each user may have running at once. Every question holds the GPU for seconds,
    so one script or retrying tab must not queue dozens ahead of everyone else.

    shortcut: counts live in this process. Fine for the single API process run.sh starts;
    with several API replicas, each would allow `limit` per user.
    """

    def __init__(self) -> None:
        self.running: dict[str, int] = {}

    def acquire(self, user_id: str, limit: int) -> bool:
        if self.running.get(user_id, 0) >= limit:
            return False
        self.running[user_id] = self.running.get(user_id, 0) + 1
        return True

    def release(self, user_id: str) -> None:
        left = self.running.get(user_id, 0) - 1
        if left > 0:
            self.running[user_id] = left
        else:
            self.running.pop(user_id, None)


slots = AskSlots()


@router.post("/ask")
async def ask(body: AskRequest, user: CurrentUser, services: Svc) -> StreamingResponse:
    within = None
    if body.sources:
        try:
            async with session_factory()() as db:
                within = await source_folder_ids(db, body.sources, load_sources())
        except SourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not slots.acquire(user.id, services.settings.max_concurrent_asks):
        raise HTTPException(
            status_code=429,
            detail="You already have a question running. Wait for it to finish, then ask again.",
        )

    async def events() -> AsyncIterator[str]:
        try:
            # The stream outlives the request-scoped session, so it opens its own.
            async with session_factory()() as db:
                async for event in answer_question(services, db, user, body.question, within):
                    yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            slots.release(user.id)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
