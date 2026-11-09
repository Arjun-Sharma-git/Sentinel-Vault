"""The long-lived services the API shares, built once at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .answer import OllamaChat
from .authz import Authz
from .authz.factory import build_authz
from .config import Settings
from .devices import resolve_device
from .embeddings import Embedder, HashEmbedder, OllamaEmbedder
from .gate import PromptGate, SimilarityCheck, Thresholds, build_gate
from .gate_labels import known_examples
from .ingest.indexer import Indexer
from .ingest.sandbox import IsolatedParser, ParseLimits
from .rerank import build_reranker
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
        await self.gate.close()
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
    device = resolve_device(settings.torch_device)  # fails fast if the GPU build is missing
    if device.requested != "cpu":
        logger.info("Laya and Docling run on %s", device.name)
    gate = build_gate(
        settings.gate_engine,
        settings.gate_injection_threshold,
        settings.gate_exfiltration_threshold,
        device.torch_device,
        per_profile={
            "multilingual": Thresholds(
                settings.gate_multilingual_injection_threshold,
                settings.gate_multilingual_exfiltration_threshold,
            )
        },
        review_threshold=settings.gate_review_threshold,
        second_stage=settings.gate_second_stage,
        llm_url=settings.ollama_url,
        llm_model=settings.gate_second_stage_model or settings.llm_model,
        second_stage_timeout=settings.gate_second_stage_timeout_seconds,
        similarity=(
            SimilarityCheck(embedder, *known_examples(), settings.gate_similarity_threshold)
            if settings.gate_engine == "laya" and settings.gate_similarity_threshold is not None
            else None
        ),
    )
    if gate.similarity is not None:
        try:
            await gate.similarity.warm()
        except Exception as exc:  # embedding unavailable: the check retries on the first prompt
            logger.warning("similarity check not warmed: %s", exc)
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

    reranker = build_reranker(settings.reranker, settings.reranker_model, device.torch_device)
    retriever = Retriever(
        authz,
        vectors,
        embedder,
        settings.retrieval_candidates,
        settings.retrieval_top_k,
        reranker,
        settings.rerank_candidates,
        settings.retrieval_max_per_document,
    )
    parser = IsolatedParser(
        ParseLimits.from_settings(settings), device.torch_device, settings.parse_network_isolation
    )
    indexer = Indexer(embedder, vectors, settings.library_dir, settings.chunk_max_tokens, parser)
    return Services(settings, sessions, authz, vectors, embedder, gate, llm, retriever, indexer)
