"""The library as a user sees it: only folders they may view, uploads only where they may edit."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select

from .. import audit
from ..ingest.library import delete_document, enqueue_index, safe_join, sha256_file
from ..ingest.parse import SUPPORTED_TYPES, mime_type_for, signature_ok
from ..models import Chunk, Document, Folder
from ..sources import load_sources, source_of
from .deps import CurrentUser, Db, Svc, require_csrf_header
from .schemas import DocumentOut

router = APIRouter(prefix="/api", tags=["library"], dependencies=[Depends(require_csrf_header)])

NOT_FOUND = "Not found"  # 404, never 403: don't confirm that something you can't see exists


@router.get("/folders")
async def my_folders(user: CurrentUser, db: Db, services: Svc) -> list[dict[str, Any]]:
    """Folders the user can view, with document counts and whether they can upload."""
    viewable = await services.authz.folders_for(user.id, "viewer")
    editable = await services.authz.folders_for(user.id, "editor")
    if not viewable:
        return []
    counts = dict(
        (
            await db.execute(
                select(Document.folder_id, func.count())
                .where(Document.folder_id.in_(viewable))
                .group_by(Document.folder_id)
            )
        ).all()
    )
    folders = (await db.scalars(select(Folder).where(Folder.id.in_(viewable)).order_by(Folder.path))).all()
    sources = load_sources()
    return [
        {
            "source": source_of(f.path, sources),
            "id": f.id,
            "path": f.path,
            "name": f.name,
            "description": f.description,
            "parent_id": f.parent_id,
            "documents": counts.get(f.id, 0),
            "can_upload": f.id in editable,
        }
        for f in folders
    ]


@router.get("/sources")
async def my_sources(user: CurrentUser, db: Db, services: Svc) -> list[dict[str, Any]]:
    """Knowledge sources the user can read something in, for choosing what to search."""
    viewable = await services.authz.folders_for(user.id, "viewer")
    if not viewable:
        return []
    sources = load_sources()
    folders = (await db.scalars(select(Folder).where(Folder.id.in_(viewable)))).all()
    counts = dict(
        (
            await db.execute(
                select(Document.folder_id, func.count())
                .where(Document.folder_id.in_(viewable), Document.status == "indexed")
                .group_by(Document.folder_id)
            )
        ).all()
    )
    out = []
    for source in sources.values():
        mine = [f for f in folders if source_of(f.path, sources) == source.name]
        if not mine:
            continue
        out.append(
            {
                "name": source.name,
                "title": source.title,
                "description": source.description,
                "homepage": source.homepage,
                "license": source.license,
                "folder": source.folder,
                "folders": len(mine),
                "documents": sum(counts.get(f.id, 0) for f in mine),
                "suggestions": source.suggestions,
            }
        )
    return out


@router.get("/folders/{folder_id}/documents")
async def folder_documents(folder_id: str, user: CurrentUser, db: Db, services: Svc) -> list[DocumentOut]:
    if not await services.authz.check_folders(user.id, {folder_id}, "viewer"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    documents = await db.scalars(
        select(Document).where(Document.folder_id == folder_id).order_by(Document.rel_path)
    )
    return [DocumentOut.model_validate(d) for d in documents]


@router.get("/documents/{document_id}")
async def read_document(document_id: str, user: CurrentUser, db: Db, services: Svc) -> dict[str, Any]:
    """The document's indexed text, section by section."""
    document = await db.get(Document, document_id)
    if document is None or not await services.authz.check_folders(user.id, {document.folder_id}, "viewer"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    chunks = (
        await db.scalars(select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal))
    ).all()
    await audit.record(db, user.id, "document.read", target=document.rel_path)
    return {
        **DocumentOut.model_validate(document).model_dump(),
        "chunks": [{"id": c.id, "heading": c.heading, "text": c.text} for c in chunks],
    }


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_filename(name: str) -> str:
    """Basename only, conservative characters, no leading dots."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return cleaned[:150]


def place(temp: Path, target: Path) -> bool:
    """Give `temp` the name `target` unless that name exists. Atomic: a hard link fails if the
    name is taken, where a rename would silently replace the other upload."""
    try:
        os.link(temp, target)
    except FileExistsError:
        return False
    target.chmod(0o644)  # mkstemp creates 0600
    return True


@router.post("/folders/{folder_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload(folder_id: str, file: UploadFile, user: CurrentUser, db: Db, services: Svc) -> DocumentOut:
    folder = await db.get(Folder, folder_id)
    if folder is None or not await services.authz.check_folders(user.id, {folder_id}, "viewer"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if not await services.authz.check_folders(user.id, {folder_id}, "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view this folder but not upload to it")

    name = safe_filename(file.filename or "")
    if not name or mime_type_for(Path(name)) is None:
        allowed = ", ".join(sorted(SUPPORTED_TYPES))
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Supported types: {allowed}")
    target = safe_join(services.settings.library_dir, f"{folder.path}/{name}")
    exists = HTTPException(status.HTTP_409_CONFLICT, f"{name} already exists in {folder.path}")
    if target.exists():
        raise exists

    # Stream to a temporary file of this request's own (two uploads of one name can't share
    # it), enforcing the size limit as bytes arrive. Hidden name: the library scanner skips it.
    limit = services.settings.max_upload_mb * 1024 * 1024
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
    temp = Path(temp_name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while block := await file.read(1 << 20):
                written += len(block)
                if written > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, f"Larger than {services.settings.max_upload_mb} MB"
                    )
                out.write(block)
        with temp.open("rb") as handle:
            if not signature_ok(target, handle.read(8)):
                raise HTTPException(
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File contents don't match its extension"
                )
        if not place(temp, target):
            raise exists  # another upload took the name since the check above
    finally:
        temp.unlink(missing_ok=True)

    document = Document(
        folder_id=folder.id,
        rel_path=f"{folder.path}/{name}",
        title=target.stem,
        mime_type=mime_type_for(target) or "",
        size_bytes=written,
        sha256=sha256_file(target),
        status="pending",
        source="upload",
        uploaded_by=user.id,
    )
    db.add(document)
    await db.flush()
    await enqueue_index(db, document.id)
    await db.commit()
    await audit.record(db, user.id, "document.upload", target=document.rel_path, size=written)
    return DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(document_id: str, user: CurrentUser, db: Db, services: Svc) -> None:
    document = await db.get(Document, document_id)
    if document is None or not await services.authz.check_folders(user.id, {document.folder_id}, "viewer"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if not await services.authz.check_folders(user.id, {document.folder_id}, "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can view this folder but not change it")
    path = safe_join(services.settings.library_dir, document.rel_path)
    rel_path = document.rel_path
    await delete_document(db, services.vectors, document)
    await db.commit()
    path.unlink(missing_ok=True)
    await audit.record(db, user.id, "document.delete", target=rel_path)
