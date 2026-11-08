"""Sign in, sign out, and who am I."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select

from .. import audit
from ..auth import COOKIE_NAME, authenticate, create_session, end_session
from ..models import Group, GroupMember
from .deps import CurrentUser, Db, Svc, require_csrf_header
from .schemas import LoginRequest, MeOut

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(require_csrf_header)])


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: Db, services: Svc) -> MeOut:
    user = await authenticate(db, body.email, body.password)
    if user is None:
        await audit.record(db, None, "auth.login", target=body.email, outcome="denied")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    token = await create_session(db, user, services.settings)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=services.settings.cookie_secure,
        max_age=services.settings.session_hours * 3600,
    )
    await audit.record(db, user.id, "auth.login", target=user.email)
    return await me_out(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response, db: Db, session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None
) -> None:
    if session:
        await end_session(db, session)
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
async def me(user: CurrentUser, db: Db) -> MeOut:
    return await me_out(db, user)


async def me_out(db: Db, user) -> MeOut:
    groups = (
        await db.scalars(
            select(Group.name).join(GroupMember).where(GroupMember.user_id == user.id).order_by(Group.name)
        )
    ).all()
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        groups=list(groups),
    )
