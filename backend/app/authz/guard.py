"""Never let OpenFGA answer from a state older than Postgres.

Grant changes are committed to Postgres first and pushed to OpenFGA second. If the push fails
(OpenFGA down, network blip, process killed in between), OpenFGA still holds the old tuples:
after a revocation, it would keep saying "allowed". This wrapper closes that window:

- A trigger bumps `authz_state.version` in the same transaction as every access change, so
  "there are changes OpenFGA hasn't seen" is recorded atomically with the change itself.
- Each query first compares `version` with `synced_version` (one indexed single-row read).
  If OpenFGA is behind, the query is answered by `SqlAuthz` (the same rules, from the tables
  that were just changed) instead. Answers are therefore always as fresh as Postgres.
- `sync()` records the version it started from once it completes, and `keep_synced()` retries
  in the background until OpenFGA has caught up.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import AuthzState, utcnow
from . import Authz, Relation
from .openfga import AuthzUnavailable, OpenFgaAuthz
from .sql import SqlAuthz

logger = logging.getLogger(__name__)


class FreshnessGuard:
    def __init__(
        self,
        primary: OpenFgaAuthz,
        fallback: SqlAuthz,
        sessions: async_sessionmaker[AsyncSession],
        retry_seconds: float = 5.0,
    ):
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self._sessions = sessions
        self._retry_seconds = retry_seconds
        self._sync_lock = asyncio.Lock()
        self.fallback_answers = 0  # how often SqlAuthz answered because OpenFGA was behind

    async def versions(self) -> tuple[int, int]:
        """(version, synced_version). A missing row counts as "behind"."""
        async with self._sessions() as db:
            row = (
                await db.execute(
                    select(AuthzState.version, AuthzState.synced_version).where(AuthzState.id == 1)
                )
            ).one_or_none()
        return (row[0], row[1]) if row else (1, 0)

    async def is_current(self) -> bool:
        version, synced = await self.versions()
        return synced >= version

    async def _backend(self) -> Authz:
        if await self.is_current():
            return self.primary
        self.fallback_answers += 1
        return self.fallback

    # ---- Authz protocol ---------------------------------------------------------------
    async def folders_for(self, user_id: str, relation: Relation = "viewer") -> set[str]:
        return await (await self._backend()).folders_for(user_id, relation)

    async def check_folders(
        self, user_id: str, folder_ids: set[str], relation: Relation = "viewer"
    ) -> set[str]:
        return await (await self._backend()).check_folders(user_id, folder_ids, relation)

    async def sync(self) -> dict[str, int]:
        """Push the tables to OpenFGA, then mark the version that push included as synced.

        The version is read *before* the snapshot is loaded, so the snapshot is at least that
        new; marking that version synced can never claim more than was pushed.
        """
        async with self._sync_lock:
            version, _ = await self.versions()
            result = await self.primary.sync()
            async with self._sessions() as db:
                await db.execute(
                    update(AuthzState)
                    .where(AuthzState.id == 1)
                    .values(
                        synced_version=func.greatest(AuthzState.synced_version, version),
                        synced_at=utcnow(),
                    )
                )
                await db.commit()
            return result

    async def try_sync(self) -> bool:
        """Sync, logging instead of raising if OpenFGA is unreachable. True on success."""
        try:
            await self.sync()
        except AuthzUnavailable as exc:
            logger.warning("OpenFGA sync failed; SQL rules answer until it succeeds: %s", exc)
            return False
        return True

    async def keep_synced(self, stop: asyncio.Event) -> None:
        """Background task: whenever OpenFGA is behind, retry the sync."""
        while not stop.is_set():
            try:
                if not await self.is_current():
                    await self.try_sync()
            except Exception:  # never let the retry loop die (e.g. Postgres restarting)
                logger.exception("authz sync loop error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._retry_seconds)
            except TimeoutError:
                pass

    async def close(self) -> None:
        await self.primary.close()
