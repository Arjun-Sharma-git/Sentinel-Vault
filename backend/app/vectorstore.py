"""Qdrant: one collection of chunk vectors, filtered by folder inside the search.

Qdrant applies the `folder_id` filter while it walks the HNSW graph (and switches to exact
search for small filtered sets), so a user who can see one department still gets k results.
pgvector's HNSW filters *after* the index scan and can silently return fewer.

The payload index on `folder_id` is created before any vector is inserted; Qdrant builds
filter-aware graph links only for indexes that exist when the graph is built.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

FOLDER_KEY = "folder_id"
DOCUMENT_KEY = "document_id"


@dataclass
class VectorHit:
    chunk_id: str
    score: float


class VectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str):
        self._client = client
        self.collection = collection

    @classmethod
    def connect(cls, url: str, api_key: str, collection: str) -> VectorStore:
        # Plain HTTP on localhost: qdrant-client warns about API keys over http otherwise.
        return cls(AsyncQdrantClient(url=url, api_key=api_key, https=url.startswith("https")), collection)

    @classmethod
    def in_memory(cls, collection: str = "chunks") -> VectorStore:
        """Qdrant's local mode, same API and filtering semantics; used by the tests."""
        return cls(AsyncQdrantClient(location=":memory:"), collection)

    async def ensure_collection(self, dimensions: int) -> None:
        if await self._client.collection_exists(self.collection):
            info = await self._client.get_collection(self.collection)
            size = info.config.params.vectors.size  # type: ignore[union-attr]
            if size != dimensions:
                raise RuntimeError(
                    f"Qdrant collection '{self.collection}' has {size}-d vectors but the embedding "
                    f"model produces {dimensions}-d. Re-index with `vault reindex --recreate`."
                )
            return
        await self._client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE),
        )
        # is_tenant: Qdrant co-locates each folder's vectors, the multitenancy layout.
        await self._client.create_payload_index(
            self.collection,
            FOLDER_KEY,
            field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        await self._client.create_payload_index(
            self.collection, DOCUMENT_KEY, field_schema=models.PayloadSchemaType.KEYWORD
        )

    async def recreate(self, dimensions: int) -> None:
        if await self._client.collection_exists(self.collection):
            await self._client.delete_collection(self.collection)
        await self.ensure_collection(dimensions)

    async def upsert(self, document_id: str, folder_id: str, items: list[tuple[str, list[float]]]) -> None:
        """Store (chunk_id, vector) pairs for one document."""
        if not items:
            return
        await self._client.upsert(
            self.collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=chunk_id, vector=vector, payload={FOLDER_KEY: folder_id, DOCUMENT_KEY: document_id}
                )
                for chunk_id, vector in items
            ],
        )

    async def delete_document(self, document_id: str) -> None:
        await self._client.delete(
            self.collection,
            wait=True,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key=DOCUMENT_KEY, match=models.MatchValue(value=document_id))]
                )
            ),
        )

    async def move_document(self, document_id: str, folder_id: str) -> None:
        """A file moved between folders: update the payload, keep the vectors."""
        await self._client.set_payload(
            self.collection,
            wait=True,
            payload={FOLDER_KEY: folder_id},
            points=models.Filter(
                must=[models.FieldCondition(key=DOCUMENT_KEY, match=models.MatchValue(value=document_id))]
            ),
        )

    async def search(self, vector: list[float], folder_ids: set[str], limit: int) -> list[VectorHit]:
        """Nearest chunks among the given folders only. No folders means no results."""
        if not folder_ids:
            return []
        result = await self._client.query_points(
            self.collection,
            query=vector,
            limit=limit,
            with_payload=False,
            query_filter=models.Filter(
                must=[models.FieldCondition(key=FOLDER_KEY, match=models.MatchAny(any=sorted(folder_ids)))]
            ),
        )
        return [VectorHit(str(p.id), p.score) for p in result.points]

    async def count(self) -> int:
        if not await self._client.collection_exists(self.collection):
            return 0
        return (await self._client.count(self.collection, exact=True)).count

    async def close(self) -> None:
        await self._client.close()
