"""The library on disk: folders become access-controlled folders, files become documents.

    data/library/
      engineering/            -> folder "engineering"
        runbooks/             -> folder "engineering/runbooks" (inherits engineering's grants)
          deploy.md           -> document, indexed into chunks

`scan_library` makes the database match the disk: new or changed files are queued for
indexing (a SHA-256 decides "changed"), deleted files are removed from Postgres and Qdrant,
and a file moved to another folder keeps its chunks and vectors; only their folder is
updated. The scan is the source of truth; nothing depends on watching for file events.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chunk, Document, Folder, Job
from ..vectorstore import VectorStore
from .parse import mime_type_for

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    folders_added: list[str] = field(default_factory=list)
    folders_removed: list[str] = field(default_factory=list)
    queued: int = 0
    moved: int = 0
    deleted: int = 0
    unchanged: int = 0


def safe_join(root: Path, relative: str) -> Path:
    """Resolve a library-relative path, refusing anything that escapes the root."""
    root = root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise PermissionError(f"path escapes the library: {relative!r}")
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _visible(path: Path) -> bool:
    return not any(part.startswith((".", "_")) for part in path.parts)


def _walk(root: Path) -> tuple[list[str], dict[str, Path]]:
    """Folder paths and supported files (by relative path) under root. Symlinks are skipped."""
    folders: list[str] = []
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink() or not _visible(relative):
            continue
        if path.is_dir():
            folders.append(relative.as_posix())
        elif path.is_file() and mime_type_for(path) and len(relative.parts) > 1:
            files[relative.as_posix()] = path  # files directly in the root belong to no folder
    return folders, files


def _display_name(folder_path: str) -> str:
    return folder_path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()


async def ensure_folders(db: AsyncSession, paths: list[str]) -> dict[str, Folder]:
    """Create folder rows for paths that lack one; return every folder by path."""
    existing = {f.path: f for f in (await db.scalars(select(Folder))).all()}
    for folder_path in sorted(paths, key=lambda p: p.count("/")):  # parents first
        if folder_path in existing:
            continue
        parent_path = folder_path.rsplit("/", 1)[0] if "/" in folder_path else None
        folder = Folder(
            path=folder_path,
            name=_display_name(folder_path),
            parent_id=existing[parent_path].id if parent_path else None,
        )
        db.add(folder)
        await db.flush()
        existing[folder_path] = folder
    return existing


async def delete_document(db: AsyncSession, vectors: VectorStore, document: Document) -> None:
    await vectors.delete_document(document.id)
    await db.delete(document)  # chunks cascade


async def scan_library(db: AsyncSession, vectors: VectorStore, root: Path) -> ScanResult:
    result = ScanResult()
    root.mkdir(parents=True, exist_ok=True)
    folder_paths, files = _walk(root)

    known = {f.path for f in (await db.scalars(select(Folder))).all()}
    folders = await ensure_folders(db, folder_paths)
    result.folders_added = sorted(set(folders) - known)

    documents = {d.rel_path: d for d in (await db.scalars(select(Document))).all()}
    hashes = {rel: sha256_file(path) for rel, path in files.items()}

    # Files that disappeared, grouped by hash, so a re-appearance elsewhere is a move.
    vanished: dict[str, list[Document]] = {}
    for rel_path, document in documents.items():
        if rel_path not in files:
            vanished.setdefault(document.sha256, []).append(document)

    for rel_path, path in files.items():
        folder = folders[rel_path.rsplit("/", 1)[0]]
        document = documents.get(rel_path)
        digest = hashes[rel_path]
        if document is None and vanished.get(digest):
            document = vanished[digest].pop()  # moved: same bytes, new place
            document.rel_path, document.folder_id = rel_path, folder.id
            await db.execute(
                update(Chunk).where(Chunk.document_id == document.id).values(folder_id=folder.id)
            )
            await vectors.move_document(document.id, folder.id)
            result.moved += 1
            continue
        if document is None:
            document = Document(
                folder_id=folder.id,
                rel_path=rel_path,
                title=path.stem,
                mime_type=mime_type_for(path) or "",
                size_bytes=path.stat().st_size,
                sha256=digest,
                status="pending",
            )
            db.add(document)
            await db.flush()
            await enqueue_index(db, document.id)
            result.queued += 1
        elif document.sha256 != digest or document.status == "failed":
            document.sha256, document.size_bytes = digest, path.stat().st_size
            document.status, document.error = "pending", None
            await enqueue_index(db, document.id)
            result.queued += 1
        else:
            result.unchanged += 1

    for group in vanished.values():
        for document in group:  # gone for good: no file with the same bytes appeared
            await delete_document(db, vectors, document)
            result.deleted += 1

    # Folders whose directory is gone (documents in them were deleted above).
    for path in sorted(set(folders) - set(folder_paths), key=lambda p: -p.count("/")):
        await db.delete(folders[path])
        result.folders_removed.append(path)

    await db.commit()
    logger.info("scan: %s", result)
    return result


async def enqueue_index(db: AsyncSession, document_id: str) -> None:
    db.add(Job(kind="index", payload={"document_id": document_id}))
