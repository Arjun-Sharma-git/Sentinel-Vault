"""Folder authorization: who may view or edit which folders.

Two interchangeable backends answer the same questions:
  OpenFgaAuthz  the OpenFGA service (production; Zanzibar-style, resolves groups and folder
                inheritance itself)
  SqlAuthz      the same rules evaluated in Python from the Postgres tables (no extra
                service; also the test oracle that OpenFGA's answers are checked against)

Retrieval asks `viewable_folders(user)` fresh on every query and filters the vector search
with it, then `check_folders` again on the final results, so revoking a grant takes effect on
the very next question with no re-indexing.
"""

from __future__ import annotations

from typing import Literal, Protocol

Relation = Literal["viewer", "editor"]


class Authz(Protocol):
    name: str

    async def folders_for(self, user_id: str, relation: Relation = "viewer") -> set[str]:
        """Ids of every folder the user holds `relation` on (directly, via groups or parents)."""
        ...

    async def check_folders(
        self, user_id: str, folder_ids: set[str], relation: Relation = "viewer"
    ) -> set[str]:
        """The subset of `folder_ids` the user holds `relation` on."""
        ...

    async def sync(self) -> dict[str, int]:
        """Make the backend match the Postgres tables. Returns counts of changes."""
        ...
