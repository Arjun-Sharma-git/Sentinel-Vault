"""Index one document: parse, chunk, embed, and store chunks in Postgres and vectors in Qdrant."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..embeddings import Embedder
from ..models import Chunk, Document, new_id, utcnow
from ..vectorstore import VectorStore
from .chunk import chunk_sections
from .library import safe_join
from .parse import parse_file

logger = logging.getLogger(__name__)


@dataclass
class Indexer:
    embedder: Embedder
    vectors: VectorStore
    library_dir: Path
    chunk_max_tokens: int = 400

    async def index(self, db: AsyncSession, document_id: str) -> None:
        document = await db.get(Document, document_id)
        if document is None:
            return  # deleted while queued
        document.status, document.error = "indexing", None
        await db.commit()
        try:
            path = safe_join(self.library_dir, document.rel_path)
            parsed = await asyncio.to_thread(parse_file, path)  # Docling is CPU-bound
            chunks = chunk_sections(parsed.sections, self.chunk_max_tokens)
            vectors = await self.embedder.embed_documents([c.for_embedding(parsed.title) for c in chunks])
        except Exception as exc:
            logger.warning("indexing %s failed: %s", document.rel_path, exc)
            document.status, document.error = "failed", str(exc)[:2000]
            await db.commit()
            return

        # Replace the old chunks and vectors with the new ones.
        rows = [
            Chunk(
                id=new_id(),
                document_id=document.id,
                folder_id=document.folder_id,
                ordinal=i,
                heading=c.heading[:500],
                text=c.text,
            )
            for i, c in enumerate(chunks)
        ]
        await self.vectors.delete_document(document.id)
        await db.execute(delete(Chunk).where(Chunk.document_id == document.id))
        db.add_all(rows)
        await self.vectors.upsert(
            document.id,
            document.folder_id,
            [(row.id, vector) for row, vector in zip(rows, vectors, strict=True)],
        )
        document.title = parsed.title[:300]
        document.mime_type = parsed.mime_type
        document.chunk_count = len(rows)
        document.status, document.indexed_at = "indexed", utcnow()
        await db.commit()
        logger.info("indexed %s: %d chunks", document.rel_path, len(rows))
