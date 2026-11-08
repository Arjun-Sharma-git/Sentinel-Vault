"""Load the access tables into an AccessSnapshot, and turn it into OpenFGA tuples."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Folder, Grant, GroupMember
from .rules import AccessSnapshot

Tuple = tuple[str, str, str]  # (user, relation, object), OpenFGA's tuple key


async def load_snapshot(db: AsyncSession) -> AccessSnapshot:
    snap = AccessSnapshot()
    for folder_id, parent_id in (await db.execute(select(Folder.id, Folder.parent_id))).all():
        snap.parents[folder_id] = parent_id
    for group_id, user_id in (await db.execute(select(GroupMember.group_id, GroupMember.user_id))).all():
        snap.memberships.setdefault(user_id, set()).add(group_id)
    rows = await db.execute(select(Grant.folder_id, Grant.relation, Grant.subject_type, Grant.subject_id))
    for folder_id, relation, subject_type, subject_id in rows.all():
        snap.grants.setdefault((folder_id, relation), set()).add(f"{subject_type}:{subject_id}")
    return snap


def to_tuples(snap: AccessSnapshot) -> set[Tuple]:
    """Every OpenFGA relationship tuple implied by the tables."""
    tuples: set[Tuple] = set()
    for user_id, groups in snap.memberships.items():
        for group_id in groups:
            tuples.add((f"user:{user_id}", "member", f"group:{group_id}"))
    for folder_id, parent_id in snap.parents.items():
        if parent_id:
            tuples.add((f"folder:{parent_id}", "parent", f"folder:{folder_id}"))
    for (folder_id, relation), subjects in snap.grants.items():
        for subject in subjects:
            # A group grant applies to the group's members: "group:<id>#member".
            user = f"{subject}#member" if subject.startswith("group:") else subject
            tuples.add((user, relation, f"folder:{folder_id}"))
    return tuples
