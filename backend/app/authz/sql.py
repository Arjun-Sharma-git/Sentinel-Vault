"""Authorization evaluated from the Postgres tables (no OpenFGA service needed)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import Relation
from .snapshot import load_snapshot


class SqlAuthz:
    name = "sql"

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def folders_for(self, user_id: str, relation: Relation = "viewer") -> set[str]:
        # A fresh snapshot per call: always consistent with the latest committed grants.
        async with self._sessions() as db:
            return (await load_snapshot(db)).folders_for(user_id, relation)

    async def check_folders(
        self, user_id: str, folder_ids: set[str], relation: Relation = "viewer"
    ) -> set[str]:
        async with self._sessions() as db:
            snap = await load_snapshot(db)
        return {f for f in folder_ids if snap.has(user_id, f, relation)}

    async def sync(self) -> dict[str, int]:
        return {"written": 0, "deleted": 0}  # reads the tables directly; nothing to sync
