"""The long-lived services the API shares, built once at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .answer import OllamaChat
from .authz import Authz
from .authz.factory import build_authz
from .config import Settings
from .embeddings import Embedder, HashEmbedder, OllamaEmbedder
from .gate import PromptGate, build_gate
from .ingest.indexer import Indexer
from .retrieval import Retriever
from .vectorstore import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    authz: Authz
    vectors: VectorStore
    embedder: Embedder
    gate: PromptGate
    llm: OllamaChat | None
    retriever: Retriever
    indexer: Indexer

    async def close(self) -> None:
        await self.vectors.close()
        await self.embedder.close()
        if self.llm:
            await self.llm.close()
        close = getattr(self.authz, "close", None)
        if close:
            await close()


async def build_services(
    settings: Settings, sessions: async_sessionmaker[AsyncSession], vectors: VectorStore | None = None
) -> Services:
    embedder: Embedder = (
        HashEmbedder()
        if settings.embedding_backend == "hash"
        else OllamaEmbedder(settings.ollama_url, settings.embedding_model)
    )
    dimensions = await embedder.probe()  # fails fast if the embedding model is missing
    if vectors is None:
        # QDRANT_URL=:memory: runs Qdrant's embedded local mode (tests, quick demos).
        vectors = (
            VectorStore.in_memory(settings.qdrant_collection)
            if settings.qdrant_url == ":memory:"
            else VectorStore.connect(settings.qdrant_url, settings.qdrant_api_key, settings.qdrant_collection)
        )
    await vectors.ensure_collection(dimensions)

    authz = await build_authz(settings, sessions)
    gate = build_gate(
        settings.gate_engine, settings.gate_injection_threshold, settings.gate_exfiltration_threshold
    )
    llm = (
        OllamaChat(
            settings.ollama_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
            settings.llm_context_tokens,
            settings.llm_keep_alive,
        )
        if settings.llm_backend == "ollama"
        else None
    )
    if llm and not await llm.available():
        logger.warning(
            "Answer model %s is not in Ollama; answers fall back to quoting sources. "
            "Pull it with: ollama pull %s",
            settings.llm_model,
            settings.llm_model,
        )

    retriever = Retriever(authz, vectors, embedder, settings.retrieval_candidates, settings.retrieval_top_k)
    indexer = Indexer(embedder, vectors, settings.library_dir, settings.chunk_max_tokens)
    return Services(settings, sessions, authz, vectors, embedder, gate, llm, retriever, indexer)
