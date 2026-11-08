"""The guarantee: nobody ever receives text from a folder they have no grant for.

Every restricted document carries a unique canary code word. For every persona and every
canary, we ask questions aimed straight at it and check the whole response (streamed steps,
retrieved passages, answer, citations), not just the visible answer.
"""

from __future__ import annotations

import pytest

# canary -> (document's folder, personas who may see it under the demo grants)
CANARIES = {
    "AMBER-OTTER-17": ("financials", {"alice", "bob"}),
    "TEAL-HERON-58": ("hr/payroll", {"alice", "erin"}),
    "VIOLET-CRANE-42": ("management", {"alice"}),
}
PROBES = {
    "AMBER-OTTER-17": [
        "What was Q3 2026 revenue and gross margin?",
        "finance code word",
        "Q4 revenue guidance outlook",
    ],
    "TEAL-HERON-58": [
        "What are the 2026 salary bands for engineering?",
        "payroll code word",
        "L5 salary band",
    ],
    "VIOLET-CRANE-42": [
        "What is Project Northwind?",
        "acquisition plan board code word",
        "Who are we acquiring?",
    ],
}


@pytest.mark.parametrize("persona", ["alice", "bob", "erin", "carol", "dave"])
@pytest.mark.parametrize("canary", list(CANARIES))
def test_canaries_never_leak(api, persona, canary):
    _, allowed = CANARIES[canary]
    for question in PROBES[canary]:
        result = api.ask(persona, question)
        leaked = canary in result["raw"]
        if persona in allowed:
            continue  # checked for recall below
        assert not leaked, f"{persona} received {canary} for {question!r}"


@pytest.mark.parametrize("canary", list(CANARIES))
def test_allowed_personas_do_find_the_document(api, canary):
    folder, allowed = CANARIES[canary]
    for persona in allowed:
        result = api.ask(persona, PROBES[canary][1])
        folders = {p["folder"] for p in result["steps"]["retrieve"]["data"]["passages"]}
        assert folder in folders, f"{persona} should retrieve from {folder}"


def test_retrieval_is_confined_to_viewable_folders(api):
    viewable = {
        "dave": {"general"},
        "carol": {"general", "engineering", "engineering/runbooks", "financials/engineering-budget"},
    }
    for persona, folders in viewable.items():
        result = api.ask(persona, "budget revenue salary acquisition outage handbook")
        got = {p["folder"] for p in result["steps"]["retrieve"]["data"]["passages"]}
        assert got <= folders, f"{persona} retrieved from {got - folders}"


def test_per_user_grant_reaches_one_subfolder_only(api):
    result = api.ask("carol", "FY27 engineering budget headcount")
    folders = {p["folder"] for p in result["steps"]["retrieve"]["data"]["passages"]}
    assert "financials/engineering-budget" in folders
    assert "financials" not in folders  # the parent folder stays closed


def test_revocation_takes_effect_on_the_next_question(api):
    grants = api.get("alice", "/api/admin/grants").json()
    carol = api.user_id("carol")
    grant = next(g for g in grants if g["subject_id"] == carol)
    assert api.delete("alice", f"/api/admin/grants/{grant['id']}").status_code == 204

    result = api.ask("carol", "FY27 engineering budget headcount")
    assert "financials/engineering-budget" not in {
        p["folder"] for p in result["steps"]["retrieve"]["data"]["passages"]
    }
    # restore for the other tests
    restored = api.post(
        "alice",
        "/api/admin/grants",
        json={
            "folder_id": grant["folder_id"],
            "subject_type": "user",
            "subject_id": carol,
            "relation": "viewer",
        },
    )
    assert restored.status_code == 201


def test_group_membership_change_is_immediate(api):
    dave = api.user_id("dave")
    groups = {g["name"]: g for g in api.get("alice", "/api/admin/groups").json()}
    finance = groups["finance"]
    assert "AMBER-OTTER-17" not in api.ask("dave", "finance code word")["raw"]
    api.put("alice", f"/api/admin/groups/{finance['id']}/members", json=finance["member_ids"] + [dave])
    try:
        assert "AMBER-OTTER-17" in api.ask("dave", "finance code word")["raw"]
    finally:
        api.put("alice", f"/api/admin/groups/{finance['id']}/members", json=finance["member_ids"])
    assert "AMBER-OTTER-17" not in api.ask("dave", "finance code word")["raw"]


def test_admin_rights_do_not_imply_reading_rights(api):
    """Admins manage grants; reading still needs a grant. Remove Alice from leadership and check."""
    groups = {g["name"]: g for g in api.get("alice", "/api/admin/groups").json()}
    leadership = groups["leadership"]
    alice = api.user_id("alice")
    api.put(
        "alice",
        f"/api/admin/groups/{leadership['id']}/members",
        json=[m for m in leadership["member_ids"] if m != alice],
    )
    try:
        assert "VIOLET-CRANE-42" not in api.ask("alice", "acquisition plan board code word")["raw"]
    finally:
        api.put("alice", f"/api/admin/groups/{leadership['id']}/members", json=leadership["member_ids"])
