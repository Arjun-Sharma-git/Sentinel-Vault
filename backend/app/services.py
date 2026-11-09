"""The long-lived services the API shares, built once at startup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
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
    fact_checker: OllamaChat | None = None  # MiniCheck, when CLAIM_JUDGE=minicheck
    # What was configured but isn't running (shown in /api/health and the page header).
    degraded: list[str] = field(default_factory=list)

    async def close(self) -> None:
        await self.gate.close()
        await self.vectors.close()
        await self.embedder.close()
        if self.llm:
            await self.llm.close()
        if self.fact_checker:
            await self.fact_checker.close()
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
        # Ollama keeps models in GPU memory for minutes after a run (an eval, a previous start).
        # Release them first: the PyTorch models below can't load into a full GPU, and Ollama
        # reloads its own models on demand (spilling to CPU if it must).
        await free_ollama_gpu(settings.ollama_url)
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
    fact_checker = None
    if settings.grounding_check and settings.claim_judge == "minicheck":
        fact_checker = OllamaChat(
            settings.ollama_url,
            settings.minicheck_model,
            settings.llm_timeout_seconds,
            8192,
            settings.llm_keep_alive,
        )
        if not await fact_checker.available():
            logger.warning(
                "Claim judge %s is not in Ollama; the answer model judges claims. Pull: ollama pull %s",
                settings.minicheck_model,
                settings.minicheck_model,
            )
            await fact_checker.close()
            fact_checker = None
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
    degraded = []
    if settings.reranker != "off" and not reranker.enabled:
        degraded.append(f"reranker {settings.reranker_model} failed to load: results are not reranked")
    if settings.grounding_check and settings.claim_judge == "minicheck" and fact_checker is None:
        degraded.append(f"claim judge {settings.minicheck_model} missing: the answer model judges claims")
    if llm is not None and not await llm.available():
        degraded.append(f"answer model {settings.llm_model} missing: answers quote sources instead")
    for problem in degraded:
        logger.error("DEGRADED: %s", problem)
    return Services(
        settings, sessions, authz, vectors, embedder, gate, llm, retriever, indexer, fact_checker, degraded
    )


async def free_ollama_gpu(url: str) -> list[str]:
    """Ask Ollama to unload every model it holds. Returns their names; never raises."""
    try:
        async with httpx.AsyncClient(base_url=url, timeout=10.0) as http:
            names = [m["name"] for m in (await http.get("/api/ps")).json().get("models", [])]
            for name in names:
                await http.post("/api/generate", json={"model": name, "keep_alive": 0})
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("could not free Ollama's GPU memory: %s", exc)
        return []
    if names:
        logger.info("unloaded Ollama models before loading GPU models: %s", ", ".join(names))
    return names
