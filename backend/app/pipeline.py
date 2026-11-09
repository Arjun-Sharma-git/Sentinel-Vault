"""One question, end to end, as a stream of events for the UI.

    gate      Laya: is the prompt an injection or bulk-export attempt?    -> deny stops here
    access    the folders this user may view, fresh from the authz backend
    retrieve  hybrid search inside those folders, then a second permission check
    generate  local LLM answer from the passages, streamed token by token
    verify    keep only citations to passages that were actually sent
    audit     who asked what, the outcome, and exactly which documents were used

Events: start, step (one per stage, possibly "running" first), token*, answer, done.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .answer import LlmError, build_messages, extractive_answer, verify
from .models import User
from .retrieval import Passage
from .services import Services

logger = logging.getLogger(__name__)

Event = dict[str, Any]

STEPS = {
    "gate": "Prompt gate (Laya)",
    "access": "Resolve folder access",
    "retrieve": "Search permitted folders",
    "generate": "Write answer",
    "verify": "Verify citations",
    "audit": "Audit log",
}


def step(step_id: str, status: str, summary: str, data: Any = None, ms: float | None = None) -> Event:
    return {
        "type": "step",
        "id": step_id,
        "title": STEPS[step_id],
        "status": status,
        "summary": summary,
        "data": data,
        "ms": ms,
    }


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def citation(n: int, passage: Passage) -> dict[str, Any]:
    return {
        "n": n,
        "chunk_id": passage.chunk_id,
        "document_id": passage.document_id,
        "title": passage.document_title,
        "heading": passage.heading,
        "folder": passage.folder_path,
        "path": passage.rel_path,
        "snippet": passage.text[:280],
    }


async def answer_question(
    services: Services, db: AsyncSession, user: User, question: str
) -> AsyncIterator[Event]:
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    yield {"type": "start", "request_id": request_id}

    # 1. Gate ------------------------------------------------------------------------------
    t = time.perf_counter()
    verdict = await services.gate.check(question)
    gate_data = {
        "engine": verdict.engine,
        "injection": verdict.injection,
        "exfiltration": verdict.exfiltration,
        "thresholds": {
            "injection": services.gate.injection_threshold,
            "exfiltration": services.gate.exfil_threshold,
        },
    }
    if not verdict.allowed:
        yield step("gate", "fail", verdict.reasons[0], gate_data, _ms(t))
        for skipped in ("access", "retrieve", "generate", "verify"):
            yield step(skipped, "skip", "Skipped: request blocked")
        yield {
            "type": "answer",
            "allowed": False,
            "text": "This request was blocked before any document was searched.",
            "reasons": verdict.reasons,
            "citations": [],
        }
        await audit.record(
            db,
            user.id,
            "query",
            outcome="denied",
            question=question,
            reasons=verdict.reasons,
            request_id=request_id,
        )
        yield step("audit", "pass", f"Recorded {request_id}")
        yield {"type": "done", "request_id": request_id, "outcome": "denied", "latency_ms": _ms(started)}
        return
    yield step(
        "gate",
        "pass",
        f"injection {verdict.injection:.2f} · bulk export {verdict.exfiltration:.2f}",
        gate_data,
        _ms(t),
    )

    # 2-3. Access and retrieval -------------------------------------------------------------
    yield step("retrieve", "running", "Searching the folders you can access…")
    t = time.perf_counter()
    result = await services.retriever.retrieve(db, user.id, question)
    yield step(
        "access",
        "pass" if result.allowed_folders else "warn",
        f"{len(result.allowed_folders)} folder(s) viewable",
        {"backend": services.authz.name, "folders": sorted(result.allowed_folders)},
        result.timings_ms.get("authz_ms"),
    )
    passages = result.passages
    yield step(
        "retrieve",
        "pass" if passages else "warn",
        f"{len(passages)} passage(s) from {len({p.document_id for p in passages})} document(s) · "
        f"dense {result.dense_hits} · full-text {result.text_hits}",
        {
            "passages": [
                {**citation(n, p), "score": p.score, "rerank": p.rerank_score, "via": p.via}
                for n, p in enumerate(passages, start=1)
            ],
            "dropped_by_recheck": result.dropped_by_recheck,
            "timings": result.timings_ms,
        },
        _ms(t),
    )

    # 4. Generate --------------------------------------------------------------------------
    t = time.perf_counter()
    raw, provider, error = "", "extractive", None
    if not passages:
        yield step("generate", "skip", "Nothing to answer from")
    else:
        if services.llm is not None:
            provider = services.llm.model
            yield step("generate", "running", f"Writing with {provider}…")
            try:
                messages = build_messages(question, passages, services.settings.datamark_sources)
                async for piece in services.llm.stream(messages):
                    raw += piece
                    yield {"type": "token", "text": piece}
            except LlmError as exc:
                logger.warning("LLM failed (request %s): %s", request_id, exc)
                if raw:
                    yield {"type": "reset"}  # discard the partial answer in the UI
                error, raw, provider = str(exc), "", "extractive"
        if not raw:
            raw = extractive_answer(passages)
            yield {"type": "token", "text": raw}
        yield step(
            "generate",
            "warn" if error else "pass",
            f"{provider} · {len(passages)} source(s)",
            {"provider": provider, "error": error},
            _ms(t),
        )

    # 5. Verify ----------------------------------------------------------------------------
    t = time.perf_counter()
    verified = verify(raw, len(passages)) if passages else None
    if verified is None or verified.no_answer:
        text = "I couldn't find this in the documents you have access to."
        cited: list[dict[str, Any]] = []
        unverified = verified.text if verified and verified.text else None
        yield step(
            "verify",
            "warn",
            "No cited answer in your permitted documents",
            {"model_reply": unverified, "dropped": verified.dropped if verified else []},
            _ms(t),
        )
    else:
        text = verified.text
        cited = [citation(n, passages[n - 1]) for n in verified.cited]
        status = "pass" if verified.grounded and not verified.dropped else "warn"
        summary = (
            f"{len(cited)} citation(s) verified"
            + (f" · {len(verified.dropped)} invalid removed" if verified.dropped else "")
            + ("" if verified.grounded else " · answer cites no source")
        )
        yield step(
            "verify", status, summary, {"dropped": verified.dropped, "grounded": verified.grounded}, _ms(t)
        )

    yield {
        "type": "answer",
        "allowed": True,
        "text": text,
        "citations": cited,
        "no_answer": not cited and (verified is None or verified.no_answer),
        "grounded": bool(verified and verified.grounded),
    }

    # 6. Audit -----------------------------------------------------------------------------
    await audit.record(
        db,
        user.id,
        "query",
        question=question,
        request_id=request_id,
        folders=sorted(result.allowed_folders),
        documents=sorted({c["document_id"] for c in cited}),
        provider=provider,
    )
    yield step("audit", "pass", f"Recorded {request_id}")
    yield {"type": "done", "request_id": request_id, "outcome": "answered", "latency_ms": _ms(started)}
