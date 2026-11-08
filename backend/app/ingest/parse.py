"""Turn a file into structured sections of text.

Plain formats (Markdown, text, CSV, JSON) are read directly: instant and exact. Rich formats
(PDF, DOCX, PPTX, XLSX, HTML) go through Docling, which recovers headings, reading order and
tables, then exports Markdown; OCR is used only where a PDF page has no text layer. Docling
runs fully offline once its models are cached, and is loaded lazily because it is heavy.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PLAIN_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}
RICH_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html",
    ".htm": "text/html",
}
SUPPORTED_TYPES = PLAIN_TYPES | RICH_TYPES

# File signatures checked on upload, so a renamed executable can't pass as a PDF.
MAGIC = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04", ".pptx": b"PK\x03\x04", ".xlsx": b"PK\x03\x04"}

MAX_CSV_ROWS = 2000
DOCLING_MAX_PAGES = 500


class ParseError(RuntimeError):
    pass


@dataclass
class Section:
    heading: str  # "Benefits › Paid time off", or "" before the first heading
    text: str


@dataclass
class ParsedDocument:
    title: str
    mime_type: str
    sections: list[Section]


def mime_type_for(path: Path) -> str | None:
    return SUPPORTED_TYPES.get(path.suffix.lower())


def signature_ok(path: Path, head: bytes) -> bool:
    """Does the file start with the bytes its extension promises?"""
    magic = MAGIC.get(path.suffix.lower())
    return magic is None or head.startswith(magic)


# ---- Markdown -> sections --------------------------------------------------------------
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_sections(markdown: str) -> tuple[str, list[Section]]:
    """Split Markdown at headings, keeping the heading path ("H1 › H2") for each section."""
    title = ""
    path: list[str] = []
    sections: list[Section] = []
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append(Section(" › ".join(path), body))
        lines.clear()

    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match:
            flush()
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1 and not title:
                title = heading
            path[:] = path[: level - 1] + [heading]
        else:
            lines.append(line)
    flush()
    return title, sections


def _strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text


# ---- Parsers ---------------------------------------------------------------------------
def _parse_plain(path: Path, raw: bytes) -> ParsedDocument:
    text = raw.decode("utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown"):
        title, sections = markdown_sections(_strip_front_matter(text))
    elif suffix == ".csv":
        title, sections = "", _csv_sections(text)
    elif suffix == ".json":
        try:
            pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc}") from exc
        title, sections = "", [Section("", pretty)]
    else:
        title, sections = "", [Section("", text.strip())] if text.strip() else []
    return ParsedDocument(title or _title_from_name(path), PLAIN_TYPES[suffix], sections)


def _csv_sections(text: str) -> list[Section]:
    """Rows as "column: value" lines, so each row reads as a self-contained fact."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return []
    rows = []
    for i, row in enumerate(reader):
        if i >= MAX_CSV_ROWS:
            break
        rows.append("; ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False) if v))
    return [Section("", "\n".join(rows))]


@lru_cache(maxsize=1)
def _docling_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pdf_options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)})


def _parse_rich(path: Path) -> ParsedDocument:
    try:
        result = _docling_converter().convert(str(path), max_num_pages=DOCLING_MAX_PAGES)
    except Exception as exc:  # Docling raises many types; all mean "could not parse"
        raise ParseError(f"Docling could not parse {path.name}: {type(exc).__name__}: {exc}") from exc
    title, sections = markdown_sections(result.document.export_to_markdown())
    return ParsedDocument(title or _title_from_name(path), RICH_TYPES[path.suffix.lower()], sections)


def parse_file(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix in PLAIN_TYPES:
        return _parse_plain(path, path.read_bytes())
    if suffix in RICH_TYPES:
        return _parse_rich(path)
    raise ParseError(f"unsupported file type: {suffix or '(none)'}")


def _title_from_name(path: Path) -> str:
    return re.sub(r"[-_]+", " ", path.stem).strip().capitalize() or path.name
