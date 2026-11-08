"""Permission-aware hybrid retrieval.

  1. The user's viewable folders, asked fresh from the authorization backend.
  2. Two searches, both restricted to those folders *inside* the query:
       dense      Qdrant vector search (meaning)
       full-text  Postgres tsvector search (exact words, names, codes)
  3. Reciprocal Rank Fusion of the two rankings (k = 60).
  4. Before anything reaches the model: load the chunks from Postgres, keep only indexed
     documents, and re-check every remaining folder against the authorization backend.

A chunk from a folder the user can't see is never scored, never fused, never sent to the
model and never cited.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from sqlalchemy import Float, bindparam, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .authz import Authz
from .embeddings import Embedder
from .models import Chunk, Document, Folder
from .vectorstore import VectorStore

RRF_K = 60
_WORD = re.compile(r"[A-Za-z0-9]{2,}")


@dataclass
class Passage:
    chunk_id: str
    document_id: str
    document_title: str
    folder_id: str
    folder_path: str
    rel_path: str
    heading: str
    text: str
    score: float
    via: list[str] = field(default_factory=list)  # "dense", "full-text"


@dataclass
class RetrievalResult:
    passages: list[Passage]
    allowed_folders: set[str]
    dense_hits: int = 0
    text_hits: int = 0
    dropped_by_recheck: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


def rrf(rankings: dict[str, list[str]], k: int = RRF_K) -> dict[str, tuple[float, list[str]]]:
    """Fuse ranked id lists: score = sum of 1 / (k + rank). Returns id -> (score, sources)."""
    fused: dict[str, tuple[float, list[str]]] = {}
    for source, ids in rankings.items():
        for rank, item in enumerate(ids, start=1):
            score, sources = fused.get(item, (0.0, []))
            fused[item] = (score + 1.0 / (k + rank), sources + [source])
    return fused


def or_query(question: str) -> str | None:
    """A tsquery matching any of the question's words (stop-words are dropped by Postgres)."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(question)))[:24]
    return " | ".join(words) if words else None


async def full_text_search(db: AsyncSession, question: str, folder_ids: set[str], limit: int) -> list[str]:
    query = or_query(question)
    if not query or not folder_ids:
        return []
    tsquery = text("to_tsquery('english', :q)").bindparams(q=query)
    rank = cast(text("ts_rank_cd(chunks.tsv, to_tsquery('english', :q))").bindparams(q=query), Float)
    rows = await db.execute(
        select(Chunk.id)
        .where(Chunk.folder_id.in_(bindparam("folders", value=sorted(folder_ids), expanding=True)))
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )
    return [row[0] for row in rows]


@dataclass
class Retriever:
    authz: Authz
    vectors: VectorStore
    embedder: Embedder
    candidates: int = 30
    top_k: int = 6

    async def retrieve(self, db: AsyncSession, user_id: str, question: str) -> RetrievalResult:
        timings: dict[str, float] = {}
        start = time.perf_counter()
        allowed = await self.authz.folders_for(user_id, "viewer")
        timings["authz_ms"] = _ms(start)
        if not allowed:
            return RetrievalResult([], allowed, timings_ms=timings)

        start = time.perf_counter()
        vector = await self.embedder.embed_query(question)
        dense = [hit.chunk_id for hit in await self.vectors.search(vector, allowed, self.candidates)]
        timings["dense_ms"] = _ms(start)

        start = time.perf_counter()
        lexical = await full_text_search(db, question, allowed, self.candidates)
        timings["text_ms"] = _ms(start)

        fused = rrf({"dense": dense, "full-text": lexical})
        ranked = sorted(fused, key=lambda cid: fused[cid][0], reverse=True)

        start = time.perf_counter()
        passages = await self._load(db, ranked[: self.top_k * 3], fused)
        # Second check, on exactly the folders that are about to reach the model.
        confirmed = await self.authz.check_folders(user_id, {p.folder_id for p in passages}, "viewer")
        kept = [p for p in passages if p.folder_id in confirmed and p.folder_id in allowed]
        timings["recheck_ms"] = _ms(start)
        return RetrievalResult(
            kept[: self.top_k],
            allowed,
            len(dense),
            len(lexical),
            dropped_by_recheck=len(passages) - len(kept),
            timings_ms=timings,
        )

    @staticmethod
    async def _load(
        db: AsyncSession, chunk_ids: list[str], fused: dict[str, tuple[float, list[str]]]
    ) -> list[Passage]:
        if not chunk_ids:
            return []
        rows = await db.execute(
            select(Chunk, Document, Folder)
            .join(Document, Document.id == Chunk.document_id)
            .join(Folder, Folder.id == Document.folder_id)
            .where(Chunk.id.in_(chunk_ids), Document.status == "indexed")
        )
        by_id = {}
        for chunk, document, folder in rows.all():
            score, sources = fused[chunk.id]
            # The document's current folder is authoritative (the chunk's copy may lag a move).
            by_id[chunk.id] = Passage(
                chunk.id,
                document.id,
                document.title,
                document.folder_id,
                folder.path,
                document.rel_path,
                chunk.heading,
                chunk.text,
                round(score, 5),
                sources,
            )
        return [by_id[cid] for cid in chunk_ids if cid in by_id]


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
