"""Background jobs on a Postgres table, claimed with FOR UPDATE SKIP LOCKED.

No extra broker: several workers (or API processes) can run side by side and never take the
same job. Failed jobs are retried up to MAX_ATTEMPTS times.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Job
from .indexer import Indexer

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
IDLE_SECONDS = 1.0


async def claim_job(db: AsyncSession) -> Job | None:
    job = await db.scalar(
        select(Job).where(Job.status == "queued").order_by(Job.id).with_for_update(skip_locked=True).limit(1)
    )
    if job is not None:
        job.status, job.attempts = "running", job.attempts + 1
        await db.commit()
    return job


async def run_job(db: AsyncSession, job: Job, indexer: Indexer) -> None:
    try:
        if job.kind == "index":
            await indexer.index(db, job.payload["document_id"])
        else:
            raise ValueError(f"unknown job kind {job.kind!r}")
    except Exception as exc:
        await db.rollback()
        job = await db.merge(job)
        job.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        job.status = "queued" if job.attempts < MAX_ATTEMPTS else "failed"
        logger.exception("job %s (%s) failed", job.id, job.kind)
    else:
        job = await db.merge(job)
        job.status = "done"
    await db.commit()


async def drain(sessions: async_sessionmaker[AsyncSession], indexer: Indexer) -> int:
    """Run queued jobs until none are left. Returns how many ran."""
    ran = 0
    while True:
        async with sessions() as db:
            job = await claim_job(db)
            if job is None:
                return ran
            await run_job(db, job, indexer)
            ran += 1


async def work_forever(
    sessions: async_sessionmaker[AsyncSession], indexer: Indexer, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            if await drain(sessions, indexer) == 0:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SECONDS)
        except TimeoutError:
            continue
        except Exception:
            logger.exception("worker loop error")
            await asyncio.sleep(IDLE_SECONDS)
