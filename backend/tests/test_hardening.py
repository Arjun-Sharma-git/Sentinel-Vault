"""Failure modes: OpenFGA failing mid-change, and password guessing."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.authz.openfga import AuthzUnavailable
from app.models import LoginThrottle

BUDGET = "financials/engineering-budget"


def _guard(api):
    authz = api.client.app.state.services.authz
    if authz.name != "openfga":
        pytest.skip("only the OpenFGA backend has a sync that can fail")
    return authz


def _call(api, fn, *args):
    return api.client.portal.call(fn, *args)


def _budget_folders(api, who: str) -> set[str]:
    result = api.ask(who, "FY27 engineering budget headcount")
    return {p["folder"] for p in result["steps"]["retrieve"]["data"]["passages"]}


# ---- OpenFGA sync failures ---------------------------------------------------------------
def test_every_access_change_bumps_the_version(api):
    guard = _guard(api)
    before, _ = _call(api, guard.versions)
    carol = api.user_id("carol")
    folder = api.folder_id("engineering")
    created = api.post(
        "alice",
        "/api/admin/grants",
        json={"folder_id": folder, "subject_type": "user", "subject_id": carol, "relation": "viewer"},
    )
    assert created.status_code == 201
    assert api.delete("alice", f"/api/admin/grants/{created.json()['id']}").status_code == 204
    after, synced = _call(api, guard.versions)
    assert after >= before + 2  # one per committed change, recorded by the database trigger
    assert synced == after  # and OpenFGA has caught up


def test_revocation_holds_while_openfga_sync_is_failing(api, monkeypatch):
    guard = _guard(api)
    carol = api.user_id("carol")
    grant = next(g for g in api.get("alice", "/api/admin/grants").json() if g["subject_id"] == carol)
    assert BUDGET in _budget_folders(api, "carol")

    async def broken_sync() -> dict[str, int]:
        raise AuthzUnavailable("simulated outage")

    monkeypatch.setattr(guard.primary, "sync", broken_sync)
    # The revoke is committed and reported as done, not turned into a 503...
    assert api.delete("alice", f"/api/admin/grants/{grant['id']}").status_code == 204
    # ...OpenFGA still holds the stale tuple and would say "allowed"...
    assert _call(api, guard.primary.check_folders, carol, {grant["folder_id"]}) == {grant["folder_id"]}
    assert not _call(api, guard.is_current)
    # ...but answers come from Postgres while it is behind, so the revocation holds.
    fallbacks = guard.fallback_answers
    assert BUDGET not in _budget_folders(api, "carol")
    assert guard.fallback_answers > fallbacks
    audit = api.get("alice", "/api/admin/audit?action=grant.revoke").json()
    assert audit and audit[0]["detail"]["subject_id"] == carol  # still audited

    # OpenFGA comes back: one sync and it is trusted again.
    monkeypatch.undo()
    _call(api, guard.sync)
    assert _call(api, guard.is_current)
    assert _call(api, guard.primary.check_folders, carol, {grant["folder_id"]}) == set()

    restored = api.post(
        "alice",
        "/api/admin/grants",
        json={k: grant[k] for k in ("folder_id", "subject_type", "subject_id", "relation")},
    )
    assert restored.status_code == 201
    assert BUDGET in _budget_folders(api, "carol")


# ---- Sign-in throttling ------------------------------------------------------------------
def _login(api, email: str, password: str):
    return api.post(None, "/api/auth/login", json={"email": email, "password": password})


def _clear_throttle(api) -> None:
    async def clear():
        async with api.client.app.state.services.sessions() as db:
            await db.execute(delete(LoginThrottle))
            await db.commit()

    _call(api, clear)


def test_account_locks_after_repeated_failures(api):
    settings = api.client.app.state.services.settings
    created = api.post(
        "alice",
        "/api/admin/users",
        json={
            "email": "mallory-target@acme.example.com",
            "name": "Lockout Target",
            "password": "correct-horse-battery",
            "is_admin": False,
            "group_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    try:
        for _ in range(settings.login_max_failures):
            assert _login(api, "mallory-target@acme.example.com", "guess-guess-guess").status_code == 401
        # Locked: even the right password is refused, without being checked.
        locked = _login(api, "mallory-target@acme.example.com", "correct-horse-battery")
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0
        # Other accounts are unaffected.
        assert _login(api, "dave@acme.example.com", "vault-demo-2026").status_code == 200
        audit = api.get("alice", "/api/admin/audit?action=auth.login").json()
        assert any(e["detail"].get("reason") == "throttled" for e in audit)
    finally:
        _clear_throttle(api)
    assert _login(api, "mallory-target@acme.example.com", "correct-horse-battery").status_code == 200


def test_unknown_accounts_lock_the_same_way(api):
    """Lockouts must not reveal which emails exist."""
    settings = api.client.app.state.services.settings
    try:
        for _ in range(settings.login_max_failures):
            assert _login(api, "nobody@acme.example.com", "guess-guess-guess").status_code == 401
        assert _login(api, "nobody@acme.example.com", "guess-guess-guess").status_code == 429
    finally:
        _clear_throttle(api)


def test_password_spraying_from_one_address_is_throttled(api, monkeypatch):
    settings = api.client.app.state.services.settings
    monkeypatch.setattr(settings, "login_ip_max_failures", 3)
    try:
        for i in range(3):  # one guess each against different accounts
            assert _login(api, f"spray-{i}@acme.example.com", "Winter2026!").status_code == 401
        assert _login(api, "dave@acme.example.com", "vault-demo-2026").status_code == 429
    finally:
        _clear_throttle(api)
