"""Text embeddings: Ollama in normal use, a deterministic hashing stand-in for tests."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

# Qwen3-Embedding is instruction-aware: queries get a task prefix, documents don't.
QUERY_INSTRUCTION = (
    "Instruct: Given a question from an employee, retrieve passages from company "
    "documents that answer it\nQuery: "
)
BATCH = 16


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    name: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    def __init__(self, url: str, model: str, timeout: float = 120.0):
        self.name = f"ollama:{model}"
        self.model = model
        self.dimensions = 0  # learned from the first response
        self._http = httpx.AsyncClient(base_url=url, timeout=timeout)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            try:
                response = await self._http.post(
                    "/api/embed",
                    json={"model": self.model, "input": texts[start : start + BATCH], "truncate": True},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"Ollama embedding failed ({self.model}): {exc}") from exc
            vectors.extend(response.json()["embeddings"])
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    async def probe(self) -> int:
        """Embed one string to learn the vector size (and fail fast if the model is missing)."""
        await self._embed(["probe"])
        return self.dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([QUERY_INSTRUCTION + text]))[0]

    async def close(self) -> None:
        await self._http.aclose()


class HashEmbedder:
    """Bag-of-words feature hashing. No model, fully deterministic: for tests and CI only."""

    name = "hash"

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = int(hashlib.md5(word.encode()).hexdigest(), 16)
            vec[digest % self.dimensions] += 1.0 if (digest >> 64) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def probe(self) -> int:
        return self.dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def close(self) -> None:
        return None
