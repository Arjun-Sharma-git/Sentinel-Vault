"""Build the configured authorization backend."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from . import Authz
from .openfga import OpenFgaAuthz
from .sql import SqlAuthz


async def build_authz(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> Authz:
    if settings.authz_backend == "sql":
        return SqlAuthz(sessions)
    authz = OpenFgaAuthz(
        settings.openfga_url, settings.openfga_api_key, settings.openfga_store_name, sessions
    )
    await authz.initialize()
    await authz.sync()  # converge on startup, whatever happened while we were down
    return authz
