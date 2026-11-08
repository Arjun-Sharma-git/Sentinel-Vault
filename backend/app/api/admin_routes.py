"""Administration: users, groups, folders, grants, indexing and the audit log.

Every change to who-can-see-what is written to Postgres, then synced to the authorization
backend before the response returns, and recorded in the audit log.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .. import audit
from ..auth import end_all_sessions, hash_password
from ..ingest.library import enqueue_index, ensure_folders, safe_join, scan_library
from ..ingest.worker import drain
from ..models import AuditEvent, Document, Folder, Grant, Group, GroupMember, Job, User
from .deps import AdminUser, Db, Svc, require_csrf_header
from .schemas import (
    DocumentOut,
    FolderCreate,
    FolderOut,
    GrantCreate,
    GrantOut,
    GroupCreate,
    GroupOut,
    UserCreate,
    UserOut,
    UserUpdate,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_csrf_header)])


async def _sync(services: Svc) -> None:
    await services.authz.sync()


# ---- Users ------------------------------------------------------------------------------
@router.get("/users")
async def list_users(_: AdminUser, db: Db) -> list[dict[str, Any]]:
    users = (await db.scalars(select(User).order_by(User.name))).all()
    memberships: dict[str, list[str]] = {}
    for group_id, user_id in (await db.execute(select(GroupMember.group_id, GroupMember.user_id))).all():
        memberships.setdefault(user_id, []).append(group_id)
    return [{**UserOut.model_validate(u).model_dump(), "group_ids": memberships.get(u.id, [])} for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, admin: AdminUser, db: Db, services: Svc) -> UserOut:
    user = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=hash_password(body.password),
        is_admin=body.is_admin,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email exists") from exc
    db.add_all(GroupMember(group_id=g, user_id=user.id) for g in set(body.group_ids))
    await db.commit()
    await _sync(services)
    await audit.record(db, admin.id, "user.create", target=user.email, groups=body.group_ids)
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, admin: AdminUser, db: Db, services: Svc) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if user.id == admin.id and (body.is_admin is False or body.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove your own admin access")
    changes = body.model_dump(exclude_unset=True, exclude={"password", "group_ids"})
    for field, value in changes.items():
        setattr(user, field, value)
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.group_ids is not None:
        await db.execute(delete(GroupMember).where(GroupMember.user_id == user.id))
        db.add_all(GroupMember(group_id=g, user_id=user.id) for g in set(body.group_ids))
    await db.commit()
    if body.password or body.is_active is False:
        await end_all_sessions(db, user.id)  # signed out everywhere, immediately
    await _sync(services)
    await audit.record(
        db,
        admin.id,
        "user.update",
        target=user.email,
        fields=sorted(body.model_dump(exclude_unset=True, exclude={"password"})),
    )
    return UserOut.model_validate(user)


# ---- Groups -----------------------------------------------------------------------------
@router.get("/groups")
async def list_groups(_: AdminUser, db: Db) -> list[GroupOut]:
    groups = (await db.scalars(select(Group).order_by(Group.name))).all()
    members: dict[str, list[str]] = {}
    for group_id, user_id in (await db.execute(select(GroupMember.group_id, GroupMember.user_id))).all():
        members.setdefault(group_id, []).append(user_id)
    return [
        GroupOut(id=g.id, name=g.name, description=g.description, member_ids=members.get(g.id, []))
        for g in groups
    ]


@router.post("/groups", status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupCreate, admin: AdminUser, db: Db) -> GroupOut:
    group = Group(name=body.name, description=body.description)
    db.add(group)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "A group with that name exists") from exc
    await audit.record(db, admin.id, "group.create", target=group.name)
    return GroupOut(id=group.id, name=group.name, description=group.description, member_ids=[])


@router.put("/groups/{group_id}/members")
async def set_members(
    group_id: str, user_ids: list[str], admin: AdminUser, db: Db, services: Svc
) -> GroupOut:
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group")
    await db.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    db.add_all(GroupMember(group_id=group_id, user_id=u) for u in set(user_ids))
    await db.commit()
    await _sync(services)
    await audit.record(db, admin.id, "group.members", target=group.name, members=sorted(set(user_ids)))
    return GroupOut(
        id=group.id, name=group.name, description=group.description, member_ids=sorted(set(user_ids))
    )


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str, admin: AdminUser, db: Db, services: Svc) -> None:
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group")
    await db.execute(delete(Grant).where(Grant.subject_type == "group", Grant.subject_id == group_id))
    await db.delete(group)
    await db.commit()
    await _sync(services)
    await audit.record(db, admin.id, "group.delete", target=group.name)


# ---- Folders and grants -----------------------------------------------------------------
@router.get("/folders")
async def list_folders(_: AdminUser, db: Db) -> list[dict[str, Any]]:
    counts = dict(
        (await db.execute(select(Document.folder_id, func.count()).group_by(Document.folder_id))).all()
    )
    folders = (await db.scalars(select(Folder).order_by(Folder.path))).all()
    return [{**FolderOut.model_validate(f).model_dump(), "documents": counts.get(f.id, 0)} for f in folders]


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderCreate, admin: AdminUser, db: Db, services: Svc) -> FolderOut:
    if await db.scalar(select(Folder).where(Folder.path == body.path)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That folder exists")
    safe_join(services.settings.library_dir, body.path).mkdir(parents=True, exist_ok=True)
    parts = body.path.split("/")
    folders = await ensure_folders(db, ["/".join(parts[: i + 1]) for i in range(len(parts))])
    folder = folders[body.path]
    folder.description = body.description
    await db.commit()
    await _sync(services)  # a new subfolder inherits its parent's grants via the parent tuple
    await audit.record(db, admin.id, "folder.create", target=body.path)
    return FolderOut.model_validate(folder)


@router.get("/grants")
async def list_grants(_: AdminUser, db: Db) -> list[GrantOut]:
    return [GrantOut.model_validate(g) for g in await db.scalars(select(Grant).order_by(Grant.created_at))]


@router.post("/grants", status_code=status.HTTP_201_CREATED)
async def create_grant(body: GrantCreate, admin: AdminUser, db: Db, services: Svc) -> GrantOut:
    if await db.get(Folder, body.folder_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such folder")
    subject_model = User if body.subject_type == "user" else Group
    if await db.get(subject_model, body.subject_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No such {body.subject_type}")
    grant = Grant(**body.model_dump(), created_by=admin.id)
    db.add(grant)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "That grant exists") from exc
    await _sync(services)
    await audit.record(db, admin.id, "grant.create", target=body.folder_id, **body.model_dump())
    return GrantOut.model_validate(grant)


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_grant(grant_id: str, admin: AdminUser, db: Db, services: Svc) -> None:
    grant = await db.get(Grant, grant_id)
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such grant")
    detail = {
        "folder_id": grant.folder_id,
        "subject_type": grant.subject_type,
        "subject_id": grant.subject_id,
        "relation": grant.relation,
    }
    await db.delete(grant)
    await db.commit()
    await _sync(services)  # effective on the user's very next question
    await audit.record(db, admin.id, "grant.revoke", target=grant.folder_id, **detail)


@router.get("/access/{user_id}")
async def access_preview(user_id: str, _: AdminUser, services: Svc) -> dict[str, list[str]]:
    """What a user can see, as the authorization backend answers it right now."""
    return {
        "viewer": sorted(await services.authz.folders_for(user_id, "viewer")),
        "editor": sorted(await services.authz.folders_for(user_id, "editor")),
    }


# ---- Indexing ---------------------------------------------------------------------------
@router.get("/documents")
async def all_documents(_: AdminUser, db: Db, services: Svc) -> dict[str, Any]:
    documents = (await db.scalars(select(Document).order_by(Document.rel_path))).all()
    queued = await db.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
    )
    return {
        "documents": [DocumentOut.model_validate(d) for d in documents],
        "jobs_pending": queued or 0,
        "vectors": await services.vectors.count(),
    }


@router.post("/scan")
async def scan(admin: AdminUser, db: Db, services: Svc) -> dict[str, Any]:
    """Re-read the library folder from disk and queue anything new or changed."""
    result = await scan_library(db, services.vectors, services.settings.library_dir)
    await _sync(services)  # new folders get their parent tuples
    await audit.record(
        db, admin.id, "library.scan", queued=result.queued, deleted=result.deleted, moved=result.moved
    )
    return result.__dict__


@router.post("/documents/{document_id}/reindex")
async def reindex(document_id: str, admin: AdminUser, db: Db) -> dict[str, str]:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document")
    document.status = "pending"
    await enqueue_index(db, document.id)
    await db.commit()
    await audit.record(db, admin.id, "document.reindex", target=document.rel_path)
    return {"status": "queued"}


@router.post("/index-now")
async def index_now(_: AdminUser, services: Svc) -> dict[str, int]:
    """Run queued indexing jobs in this request (useful when the background worker is off)."""
    return {"ran": await drain(services.sessions, services.indexer)}


# ---- Audit ------------------------------------------------------------------------------
@router.get("/audit")
async def audit_log(
    _: AdminUser, db: Db, limit: int = Query(200, ge=1, le=1000), action: str | None = None
) -> list[dict[str, Any]]:
    query = select(AuditEvent, User.name).outerjoin(User, User.id == AuditEvent.actor_id)
    if action:
        query = query.where(AuditEvent.action == action)
    rows = await db.execute(query.order_by(AuditEvent.id.desc()).limit(limit))
    return [
        {
            "id": e.id,
            "ts": e.ts,
            "actor_id": e.actor_id,
            "actor": name,
            "action": e.action,
            "target": e.target,
            "outcome": e.outcome,
            "detail": e.detail,
        }
        for e, name in rows.all()
    ]
