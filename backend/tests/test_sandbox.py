"""Parsing untrusted files: preflight checks, resource limits, and the child process."""

from __future__ import annotations

import asyncio
import sys
import time
import zipfile
from pathlib import Path

import pytest

from app.ingest.parse import ParseError
from app.ingest.sandbox import IsolatedParser, ParseLimits, check_zip, preflight

SMALL = ParseLimits(timeout_seconds=20, max_file_mb=1, zip_max_uncompressed_mb=1, zip_max_ratio=100)


def _parse(parser: IsolatedParser, path: Path):
    return asyncio.run(parser.parse(path))


def _office_zip(path: Path, payload: bytes) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", payload)
    return path


def test_oversized_file_is_refused_before_parsing(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(ParseError, match="limit is 1 MB"):
        preflight(big, SMALL)


def test_zip_bomb_is_refused_without_decompressing(tmp_path):
    bomb = _office_zip(tmp_path / "bomb.docx", b"\0" * (5 * 1024 * 1024))  # ~5 KB on disk
    assert bomb.stat().st_size < 100_000
    with pytest.raises(ParseError, match="expands to|compression ratio"):
        check_zip(bomb, SMALL)


def test_high_compression_ratio_is_refused(tmp_path):
    lax = ParseLimits(zip_max_uncompressed_mb=100, zip_max_ratio=50)
    bomb = _office_zip(tmp_path / "ratio.docx", b"A" * 500_000)
    with pytest.raises(ParseError, match="compression ratio"):
        check_zip(bomb, lax)


def test_not_a_zip_is_refused(tmp_path):
    fake = tmp_path / "fake.docx"
    fake.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(ParseError, match="not a valid Office file"):
        check_zip(fake, SMALL)


def test_hung_parser_is_killed_at_the_timeout(tmp_path):
    hung = IsolatedParser(
        ParseLimits(timeout_seconds=1),
        network_isolation="off",
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
    )
    (tmp_path / "a.txt").write_text("hi")
    start = time.monotonic()
    with pytest.raises(ParseError, match="longer than 1s"):
        _parse(hung, tmp_path / "a.txt")
    assert time.monotonic() - start < 10


def test_parser_memory_is_capped(tmp_path):
    greedy = IsolatedParser(
        ParseLimits(memory_mb=512),
        network_isolation="off",
        command=[sys.executable, "-c", "x = bytearray(2 * 1024**3)"],
    )
    (tmp_path / "a.txt").write_text("hi")
    with pytest.raises(ParseError, match="MemoryError|failed"):
        _parse(greedy, tmp_path / "a.txt")


def test_parser_gets_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://vault:secret@db/vault")
    monkeypatch.setenv("OPENFGA_API_KEY", "secret-key")
    snoop = IsolatedParser(
        network_isolation="off",
        command=[
            sys.executable,
            "-c",
            "import json, os; print(json.dumps({'error': ','.join(sorted("
            "k for k in os.environ if k in ('DATABASE_URL', 'OPENFGA_API_KEY'))) or 'none'}))",
        ],
    )
    (tmp_path / "a.txt").write_text("hi")
    with pytest.raises(ParseError, match="^none$"):
        _parse(snoop, tmp_path / "a.txt")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="network namespaces are Linux-only")
def test_parser_has_no_network_when_isolated(tmp_path):
    parser = IsolatedParser(network_isolation="auto")
    if not parser.no_network:
        pytest.skip("unprivileged network namespaces unavailable here")
    probe = IsolatedParser(
        network_isolation="auto",
        command=[
            sys.executable,
            "-c",
            "import json, socket\n"
            "try:\n socket.create_connection(('1.1.1.1', 53), timeout=3); r = 'connected'\n"
            "except OSError as e:\n r = 'blocked'\n"
            "print(json.dumps({'error': r}))",
        ],
    )
    (tmp_path / "a.txt").write_text("hi")
    with pytest.raises(ParseError, match="^blocked$"):
        _parse(probe, tmp_path / "a.txt")


def test_real_documents_parse_in_the_child(tmp_path):
    import docx

    document = docx.Document()
    document.add_heading("Pay bands", 1)
    document.add_paragraph("Band L5 starts at the midpoint.")
    document.save(tmp_path / "bands.docx")
    (tmp_path / "notes.md").write_text("# Notes\n\nShip on Friday.\n")

    parser = IsolatedParser(ParseLimits(timeout_seconds=120))
    rich = _parse(parser, tmp_path / "bands.docx")
    plain = _parse(parser, tmp_path / "notes.md")
    assert any("Band L5" in s.text for s in rich.sections)
    assert plain.title == "Notes" and plain.sections[0].text == "Ship on Friday."
