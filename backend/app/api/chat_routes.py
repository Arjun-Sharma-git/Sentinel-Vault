"""Ask a question; the answer streams back as Server-Sent Events."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..db import session_factory
from ..pipeline import answer_question
from .deps import CurrentUser, Svc, require_csrf_header
from .schemas import AskRequest

router = APIRouter(prefix="/api", tags=["chat"], dependencies=[Depends(require_csrf_header)])


@router.post("/ask")
async def ask(body: AskRequest, user: CurrentUser, services: Svc) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        # The stream outlives the request-scoped session, so it opens its own.
        async with session_factory()() as db:
            async for event in answer_question(services, db, user, body.question):
                yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
