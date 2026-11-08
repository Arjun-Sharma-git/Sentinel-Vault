"""Request dependencies: the database session, services, the signed-in user, CSRF."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import COOKIE_NAME, CSRF_HEADER, resolve_session
from ..db import get_db
from ..models import User
from ..services import Services

Db = Annotated[AsyncSession, Depends(get_db)]


def get_services(request: Request) -> Services:
    return request.app.state.services


Svc = Annotated[Services, Depends(get_services)]


def require_csrf_header(request: Request) -> None:
    """State-changing requests must carry the custom header (see auth.py)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing {CSRF_HEADER} header")


async def current_user(
    db: Db, services: Svc, session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None
) -> User:
    user = await resolve_session(db, session or "", services.settings)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def admin_user(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator only")
    return user


AdminUser = Annotated[User, Depends(admin_user)]
