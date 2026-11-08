"""The access rules, as plain Python over a snapshot of the tables.

This mirrors model.fga exactly:
  editor(folder) = direct editor grant (to the user or a group they're in) OR editor(parent)
  viewer(folder) = direct viewer grant OR editor(folder) OR viewer(parent)

It is used by SqlAuthz, by the tuple builder for OpenFGA, and by the tests as an oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import Relation


@dataclass
class AccessSnapshot:
    parents: dict[str, str | None] = field(default_factory=dict)  # folder id -> parent id
    memberships: dict[str, set[str]] = field(default_factory=dict)  # user id -> group ids
    # (folder id, relation) -> subjects, each "user:<id>" or "group:<id>"
    grants: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def subjects_of(self, user_id: str) -> set[str]:
        return {f"user:{user_id}"} | {f"group:{g}" for g in self.memberships.get(user_id, set())}

    def _ancestors(self, folder_id: str) -> list[str]:
        """The folder and its ancestors, nearest first (cycle-safe)."""
        chain, seen = [], set()
        current: str | None = folder_id
        while current is not None and current not in seen:
            chain.append(current)
            seen.add(current)
            current = self.parents.get(current)
        return chain

    def has(self, user_id: str, folder_id: str, relation: Relation) -> bool:
        subjects = self.subjects_of(user_id)
        wanted = ("viewer", "editor") if relation == "viewer" else ("editor",)
        for folder in self._ancestors(folder_id):
            for rel in wanted:
                if self.grants.get((folder, rel), set()) & subjects:
                    return True
        return False

    def folders_for(self, user_id: str, relation: Relation) -> set[str]:
        return {f for f in self.parents if self.has(user_id, f, relation)}
