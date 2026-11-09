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

from . import audit, grounding
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
    "verify": "Verify citations and claims",
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
    services: Services, db: AsyncSession, user: User, question: str, within: set[str] | None = None
) -> AsyncIterator[Event]:
    """`within`: folder ids to search (the folders of the knowledge sources the user picked)."""
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    yield {"type": "start", "request_id": request_id}

    # 1. Gate ------------------------------------------------------------------------------
    t = time.perf_counter()
    verdict = await services.gate.check(question)
    limits = verdict.thresholds or services.gate.thresholds_for(verdict.profile)
    gate_data = {
        "engine": verdict.engine,
        "profile": verdict.profile,
        "injection": verdict.injection,
        "exfiltration": verdict.exfiltration,
        "thresholds": {"injection": limits.injection, "exfiltration": limits.exfiltration},
        "review": verdict.review,
    }
    gate_audit = {
        "profile": verdict.profile,
        "injection": round(verdict.injection, 3),
        "exfiltration": round(verdict.exfiltration, 3),
    }
    if verdict.similarity is not None:
        gate_data["similarity"] = verdict.similarity
        gate_audit["similarity"] = verdict.similarity
    if verdict.second_stage is not None:
        gate_data["second_stage"] = verdict.second_stage
        gate_audit["second_stage"] = verdict.second_stage
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
            gate=gate_audit,
        )
        yield step("audit", "pass", f"Recorded {request_id}")
        yield {"type": "done", "request_id": request_id, "outcome": "denied", "latency_ms": _ms(started)}
        return
    if verdict.review:
        # Allowed, but close to a threshold: kept for an admin to look at (and to refit with).
        await audit.record(
            db, user.id, "gate.review", question=question, request_id=request_id, gate=gate_audit
        )
    yield step(
        "gate",
        "pass",
        f"injection {verdict.injection:.2f} · bulk export {verdict.exfiltration:.2f}"
        + (" · flagged for review" if verdict.review else ""),
        gate_data,
        _ms(t),
    )

    # 2-3. Access and retrieval -------------------------------------------------------------
    yield step("retrieve", "running", "Searching the folders you can access…")
    t = time.perf_counter()
    result = await services.retriever.retrieve(db, user.id, question, within)
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
    claims_checked: grounding.Grounding | None = None
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
        claims_checked = None
        if services.settings.grounding_check and provider != "extractive":
            yield step("verify", "running", "Checking each claim against its sources…")
            claims_checked = await grounding.check(services.llm, text, passages)
            text = grounding.mark_diagrams(text, claims_checked)
        unsupported = claims_checked.unsupported if claims_checked else []
        status = "pass" if verified.grounded and not verified.dropped and not unsupported else "warn"
        summary = (
            f"{len(cited)} citation(s) verified"
            + (f" · {len(verified.dropped)} invalid removed" if verified.dropped else "")
            + ("" if verified.grounded else " · answer cites no source")
        )
        if claims_checked and claims_checked.checked:
            judged = [c for c in claims_checked.claims if c.supported is not None]
            summary += f" · {len(judged) - len(unsupported)}/{len(judged)} claims supported"
        elif claims_checked and claims_checked.error:
            summary += " · claim check unavailable"
        yield step(
            "verify",
            status,
            summary,
            {
                "dropped": verified.dropped,
                "grounded": verified.grounded,
                "claims": [
                    {"kind": c.kind, "text": c.text, "sources": c.sources, "supported": c.supported}
                    for c in (claims_checked.claims if claims_checked else [])
                ],
                "faithfulness": claims_checked.faithfulness if claims_checked else None,
                "error": claims_checked.error if claims_checked else None,
            },
            _ms(t),
        )

    yield {
        "type": "answer",
        "allowed": True,
        "text": text,
        "citations": cited,
        "no_answer": not cited and (verified is None or verified.no_answer),
        "grounded": bool(verified and verified.grounded),
        "unsupported": [
            {"kind": c.kind, "text": c.text}
            for c in (claims_checked.unsupported if cited and claims_checked else [])
        ],
        "faithfulness": claims_checked.faithfulness if cited and claims_checked else None,
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
        gate=gate_audit,
    )
    yield step("audit", "pass", f"Recorded {request_id}")
    yield {"type": "done", "request_id": request_id, "outcome": "answered", "latency_ms": _ms(started)}
