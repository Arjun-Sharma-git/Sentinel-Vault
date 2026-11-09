"""OpenFGA backend, over its HTTP API.

Reads use HIGHER_CONSISTENCY so a grant or revocation is visible on the very next request
(OpenFGA 1.22 otherwise may serve reads from a replica or cache).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import Relation
from .snapshot import Tuple, load_snapshot, to_tuples

logger = logging.getLogger(__name__)

MODEL_FILE = Path(__file__).with_name("model.json")
WRITE_BATCH = 100  # OpenFGA's limit of tuple writes + deletes per request
CHECK_BATCH = 50  # checks per BatchCheck call
CONSISTENCY = "HIGHER_CONSISTENCY"


class AuthzUnavailable(RuntimeError):
    """OpenFGA could not answer. Callers must deny (fail closed)."""


class OpenFgaAuthz:
    name = "openfga"

    def __init__(self, url: str, api_key: str, store_name: str, sessions: async_sessionmaker[AsyncSession]):
        self._http = httpx.AsyncClient(
            base_url=url, timeout=10.0, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._store_name = store_name
        self._sessions = sessions
        self.store_id = ""
        self.model_id = ""

    async def close(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise AuthzUnavailable(f"OpenFGA unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise AuthzUnavailable(f"OpenFGA {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()

    # ---- Setup ------------------------------------------------------------------------
    async def initialize(self) -> None:
        """Find or create the store, and make sure its latest model is ours."""
        stores = (await self._http.get("/stores", params={"page_size": 100})).json().get("stores", [])
        store = next((s for s in stores if s["name"] == self._store_name), None)
        if store is None:
            store = await self._post("/stores", {"name": self._store_name})
        self.store_id = store["id"]

        wanted = json.loads(MODEL_FILE.read_text())
        latest = (
            (await self._http.get(f"/stores/{self.store_id}/authorization-models", params={"page_size": 1}))
            .json()
            .get("authorization_models", [])
        )
        if latest and _same_model(latest[0], wanted):
            self.model_id = latest[0]["id"]
        else:
            self.model_id = (await self._post(f"/stores/{self.store_id}/authorization-models", wanted))[
                "authorization_model_id"
            ]
            logger.info("wrote OpenFGA authorization model %s", self.model_id)

    # ---- Queries ----------------------------------------------------------------------
    async def folders_for(self, user_id: str, relation: Relation = "viewer") -> set[str]:
        """Every folder the user has `relation` on. Uses StreamedListObjects: plain ListObjects
        stops at 1,000 results (OpenFGA's default), which silently cut a leadership user's
        search to 1,000 of the GitLab handbook's 1,500 folders."""
        body = {
            "authorization_model_id": self.model_id,
            "type": "folder",
            "relation": relation,
            "user": f"user:{user_id}",
            "consistency": CONSISTENCY,
        }
        objects: set[str] = set()
        try:
            async with self._http.stream(
                "POST", f"/stores/{self.store_id}/streamed-list-objects", json=body
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if "error" in item:
                        raise AuthzUnavailable(f"OpenFGA streamed-list-objects: {item['error']}")
                    obj = item.get("result", {}).get("object", "")
                    if obj.startswith("folder:"):
                        objects.add(obj.removeprefix("folder:"))
        except httpx.HTTPError as exc:
            raise AuthzUnavailable(f"OpenFGA unreachable: {exc}") from exc
        return objects

    async def check_folders(
        self, user_id: str, folder_ids: set[str], relation: Relation = "viewer"
    ) -> set[str]:
        ids = sorted(folder_ids)
        allowed: set[str] = set()
        for start in range(0, len(ids), CHECK_BATCH):
            batch = ids[start : start + CHECK_BATCH]
            out = await self._post(
                f"/stores/{self.store_id}/batch-check",
                {
                    "authorization_model_id": self.model_id,
                    "consistency": CONSISTENCY,
                    "checks": [
                        {
                            "tuple_key": {
                                "user": f"user:{user_id}",
                                "relation": relation,
                                "object": f"folder:{fid}",
                            },
                            "correlation_id": str(i),
                        }
                        for i, fid in enumerate(batch)
                    ],
                },
            )
            results = out.get("result", {})
            allowed |= {fid for i, fid in enumerate(batch) if results.get(str(i), {}).get("allowed")}
        return allowed

    # ---- Sync from Postgres -----------------------------------------------------------
    async def _read_all(self) -> set[Tuple]:
        tuples: set[Tuple] = set()
        token = ""
        while True:
            out = await self._post(
                f"/stores/{self.store_id}/read", {"page_size": 100, "continuation_token": token}
            )
            for t in out.get("tuples", []):
                key = t["key"]
                tuples.add((key["user"], key["relation"], key["object"]))
            token = out.get("continuation_token", "")
            if not token:
                return tuples

    async def sync(self) -> dict[str, int]:
        """Write missing tuples and delete stale ones so OpenFGA matches the tables."""
        async with self._sessions() as db:
            wanted = to_tuples(await load_snapshot(db))
        current = await self._read_all()
        writes, deletes = sorted(wanted - current), sorted(current - wanted)

        def keys(items: list[Tuple]) -> list[dict[str, str]]:
            return [{"user": u, "relation": r, "object": o} for u, r, o in items]

        # Deletes first, so a moved grant never exists twice (it may briefly exist zero times,
        # which fails closed).
        for start in range(0, len(deletes), WRITE_BATCH):
            await self._post(
                f"/stores/{self.store_id}/write",
                {
                    "authorization_model_id": self.model_id,
                    "deletes": {"tuple_keys": keys(deletes[start : start + WRITE_BATCH])},
                },
            )
        for start in range(0, len(writes), WRITE_BATCH):
            await self._post(
                f"/stores/{self.store_id}/write",
                {
                    "authorization_model_id": self.model_id,
                    "writes": {"tuple_keys": keys(writes[start : start + WRITE_BATCH])},
                },
            )
        if writes or deletes:
            logger.info("OpenFGA sync: +%d -%d tuples", len(writes), len(deletes))
        return {"written": len(writes), "deleted": len(deletes)}


def _same_model(stored: dict[str, Any], wanted: dict[str, Any]) -> bool:
    def canon(model: dict[str, Any]) -> str:
        types = sorted(model.get("type_definitions", []), key=lambda t: t["type"])
        return json.dumps(types, sort_keys=True)

    return canon(stored) == canon(wanted)
