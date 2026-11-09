"""Jobs whose worker went away are put back in the queue."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.ingest.worker import MAX_ATTEMPTS, requeue_stale
from app.models import Job


def test_jobs_left_running_by_a_dead_worker_are_requeued_or_failed(api):
    services = api.client.app.state.services
    long_ago = datetime.now(UTC) - timedelta(hours=1)

    async def run() -> dict[str, str]:
        async with services.sessions() as db:
            jobs = {
                "orphan": Job(kind="test", status="running", attempts=1, updated_at=long_ago),
                "worn out": Job(kind="test", status="running", attempts=MAX_ATTEMPTS, updated_at=long_ago),
                "busy": Job(kind="test", status="running", attempts=1),  # just claimed: left alone
            }
            db.add_all(jobs.values())
            await db.commit()
            ids = {name: job.id for name, job in jobs.items()}
        async with services.sessions() as db:
            await requeue_stale(db)
            rows = (await db.scalars(select(Job).where(Job.id.in_(ids.values())))).all()
            status = {name: next(j.status for j in rows if j.id == i) for name, i in ids.items()}
            await db.execute(delete(Job).where(Job.id.in_(ids.values())))
            await db.commit()
        return status

    assert api.client.portal.call(run) == {"orphan": "queued", "worn out": "failed", "busy": "running"}
