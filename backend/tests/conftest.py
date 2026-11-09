"""Test environment: the real Postgres and OpenFGA from docker compose, isolated from your data.

- Postgres: the `vault_test` database, migrated from scratch each session.
- OpenFGA: a throwaway store, deleted afterwards. TEST_AUTHZ=sql runs the same suite on the
  built-in SQL backend instead.
- Qdrant: embedded in-memory mode; embeddings: deterministic hashing (no model needed).
- The library: a temporary copy of data/library, so tests can add, move and delete files.

Requires `docker compose up -d` (the suite skips with a message otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
LIBRARY_SOURCE = BACKEND.parent / "data" / "library"
TEST_LIBRARY = Path(tempfile.mkdtemp(prefix="vault-library-")) / "library"
shutil.copytree(LIBRARY_SOURCE, TEST_LIBRARY)

os.environ.update(
    {
        "DATABASE_URL": "postgresql+asyncpg://vault:vault-dev-password@127.0.0.1:5433/vault_test",
        "QDRANT_URL": ":memory:",
        "EMBEDDING_BACKEND": "hash",
        "LLM_BACKEND": "extractive",
        "GATE_ENGINE": "heuristic",
        "GATE_SECOND_STAGE": "off",
        "RERANKER": "off",  # tests use a fake reranker where they need one
        "AUTHZ_BACKEND": os.environ.get("TEST_AUTHZ", "openfga"),
        "OPENFGA_STORE_NAME": f"vault-test-{uuid.uuid4().hex[:8]}",
        "LIBRARY_DIR": str(TEST_LIBRARY),
        "RUN_WORKER": "false",
        "SCAN_ON_STARTUP": "true",
        "LOG_LEVEL": "WARNING",
        "PUBLIC_ORIGIN": "http://testserver",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import COOKIE_NAME  # noqa: E402

DEMO_PASSWORD = "vault-demo-2026"
PERSONAS = ["alice", "bob", "erin", "carol", "dave"]
HEADERS = {"X-Vault-Request": "1"}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _alembic(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, check=True, capture_output=True)


@pytest.fixture(scope="session")
def client():
    if not _port_open(5433) or (os.environ["AUTHZ_BACKEND"] == "openfga" and not _port_open(8081)):
        pytest.skip("Start the services first: docker compose up -d")
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")

    from app.cli import seed_demo_into
    from app.ingest.worker import drain
    from app.main import app

    with TestClient(app) as test_client:
        services = app.state.services
        test_client.portal.call(seed_demo_into, services)
        test_client.portal.call(drain, services.sessions, services.indexer)
        yield test_client
        if services.authz.name == "openfga":  # remove the throwaway store
            openfga = services.authz.primary
            test_client.portal.call(openfga._http.delete, f"/stores/{openfga.store_id}")


@pytest.fixture(scope="session")
def tokens(client) -> dict[str, str]:
    out = {}
    for name in PERSONAS:
        response = client.post(
            "/api/auth/login",
            headers=HEADERS,
            json={"email": f"{name}@acme.example.com", "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200, response.text
        out[name] = response.cookies[COOKIE_NAME]
    client.cookies.clear()
    return out


class Api:
    """Make requests as a given persona."""

    def __init__(self, client: TestClient, tokens: dict[str, str]):
        self.client, self.tokens = client, tokens

    def _as(self, who: str | None) -> None:
        self.client.cookies.clear()
        if who:
            self.client.cookies.set(COOKIE_NAME, self.tokens[who])

    def get(self, who: str | None, path: str, **kw):
        self._as(who)
        return self.client.get(path, **kw)

    def post(self, who: str | None, path: str, **kw):
        self._as(who)
        return self.client.post(path, headers={**HEADERS, **kw.pop("headers", {})}, **kw)

    def put(self, who, path, **kw):
        self._as(who)
        return self.client.put(path, headers=HEADERS, **kw)

    def patch(self, who, path, **kw):
        self._as(who)
        return self.client.patch(path, headers=HEADERS, **kw)

    def delete(self, who, path, **kw):
        self._as(who)
        return self.client.delete(path, headers=HEADERS, **kw)

    def ask(self, who: str, question: str) -> dict:
        """Ask and collect the SSE stream into {steps, answer, done, raw}."""
        response = self.post(who, "/api/ask", json={"question": question})
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[6:])
            for block in response.text.split("\n\n")
            for line in block.split("\n")
            if line.startswith("data: ")
        ]
        steps = {e["id"]: e for e in events if e["type"] == "step"}
        answer = next(e for e in events if e["type"] == "answer")
        return {
            "steps": steps,
            "answer": answer,
            "raw": response.text,
            "done": next(e for e in events if e["type"] == "done"),
        }

    def folder_id(self, path: str) -> str:
        folders = self.get("alice", "/api/admin/folders").json()
        return next(f["id"] for f in folders if f["path"] == path)

    def user_id(self, name: str) -> str:
        users = self.get("alice", "/api/admin/users").json()
        return next(u["id"] for u in users if u["email"].startswith(f"{name}@"))


@pytest.fixture(scope="session")
def api(client, tokens) -> Api:
    return Api(client, tokens)
