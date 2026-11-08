"""OpenFGA must answer exactly like the plain-Python rules, through random admin changes."""

from __future__ import annotations

import random

import pytest

from app.authz.rules import AccessSnapshot
from app.authz.snapshot import load_snapshot

PERSONAS = ["alice", "bob", "erin", "carol", "dave"]


def _oracle(api) -> AccessSnapshot:
    services = api.client.app.state.services

    async def load():
        async with services.sessions() as db:
            return await load_snapshot(db)

    return api.client.portal.call(load)


def _backend_view(api, user_id: str) -> tuple[set[str], set[str]]:
    view = api.get("alice", f"/api/admin/access/{user_id}").json()
    return set(view["viewer"]), set(view["editor"])


def test_backend_matches_oracle_under_random_changes(api):
    authz = api.client.app.state.services.authz
    if authz.name != "openfga":
        pytest.skip("the SQL backend *is* the oracle")
    fallbacks = authz.fallback_answers  # must stay put: OpenFGA itself has to answer
    rng = random.Random(7)
    users = {name: api.user_id(name) for name in PERSONAS}
    groups = api.get("alice", "/api/admin/groups").json()
    folders = [f["id"] for f in api.get("alice", "/api/admin/folders").json()]
    original_groups = {g["id"]: g["member_ids"] for g in groups}
    created: list[str] = []
    try:
        for _ in range(12):
            if rng.random() < 0.5:  # random grant
                response = api.post(
                    "alice",
                    "/api/admin/grants",
                    json={
                        "folder_id": rng.choice(folders),
                        "subject_type": rng.choice(["user", "group"]),
                        "subject_id": None,
                        "relation": rng.choice(["viewer", "editor"]),
                    }
                    | (
                        {"subject_type": "user", "subject_id": rng.choice(list(users.values()))}
                        if rng.random() < 0.5
                        else {"subject_type": "group", "subject_id": rng.choice(groups)["id"]}
                    ),
                )
                if response.status_code == 201:
                    created.append(response.json()["id"])
            else:  # random membership change
                group = rng.choice(groups)
                members = rng.sample(list(users.values()), rng.randint(0, len(users)))
                api.put("alice", f"/api/admin/groups/{group['id']}/members", json=members)

            oracle = _oracle(api)
            for name, user_id in users.items():
                viewer, editor = _backend_view(api, user_id)
                assert viewer == oracle.folders_for(user_id, "viewer"), name
                assert editor == oracle.folders_for(user_id, "editor"), name
        assert authz.fallback_answers == fallbacks, "the SQL fallback answered, not OpenFGA"
    finally:
        for grant_id in created:
            api.delete("alice", f"/api/admin/grants/{grant_id}")
        for group_id, members in original_groups.items():
            api.put("alice", f"/api/admin/groups/{group_id}/members", json=members)
