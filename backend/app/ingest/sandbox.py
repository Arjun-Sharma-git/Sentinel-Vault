"""Parse untrusted files in a separate, limited, killable process.

Uploaded files are attacker-controlled input to complex parsers (PDF, Office, HTML). Parsing
them inside the API process would put a parser bug, a decompression bomb or a pathological
PDF in the same process that holds sessions and database connections. Instead, each file is
parsed by `python -m app.ingest.parse_child` with:

- Preflight checks in the parent, before anything opens the file: a size cap, and for Office
  files (zip archives) caps on entry count, total uncompressed size and compression ratio.
- Resource limits in the child, set before it runs: address space (`parse_memory_mb`), CPU
  time, the largest file it may write, and no core dumps.
- A wall-clock timeout: the child's whole process group is killed when it expires.
- A scrubbed environment: no database URL, API keys, passwords or tokens.
- No network where Linux allows it (an unprivileged user + network namespace via `unshare`),
  so a compromised parser can't phone home. Docling's models must then be cached in advance:
  `uv run python -m app.cli download-docling`.

The child prints one JSON object on stdout: the parsed document, or {"error": "..."}.

What this does not do: restrict the filesystem. A compromised parser can still read what the
API's user can read. For that, run the indexer as its own container (`RUN_WORKER=false` on the
API) with only the library mounted, read-only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import resource
import shutil
import signal
import subprocess
import sys
import zipfile
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Literal

from ..config import BASE_DIR, Settings
from .parse import ParsedDocument, ParseError, Section

logger = logging.getLogger(__name__)

ZIP_TYPES = {".docx", ".pptx", ".xlsx"}
SECRET_MARKERS = ("KEY", "PASSWORD", "SECRET", "TOKEN", "DATABASE_URL", "CREDENTIAL")
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class ParseLimits:
    timeout_seconds: float = 300.0
    memory_mb: int = 6144  # 0 = no address-space limit
    max_file_mb: int = 200
    max_pages: int = 500
    zip_max_uncompressed_mb: int = 500
    zip_max_ratio: int = 100
    zip_max_entries: int = 10_000
    write_limit_mb: int = 1024  # largest file the child may write (Docling's temp files)

    @classmethod
    def from_settings(cls, settings: Settings) -> ParseLimits:
        return cls(
            timeout_seconds=settings.parse_timeout_seconds,
            memory_mb=settings.parse_memory_mb,
            max_file_mb=settings.parse_max_file_mb,
            max_pages=settings.parse_max_pages,
            zip_max_uncompressed_mb=settings.zip_max_uncompressed_mb,
            zip_max_ratio=settings.zip_max_ratio,
            zip_max_entries=settings.zip_max_entries,
        )


# ---- Preflight (in the parent, before the file is opened by a parser) --------------------
def preflight(path: Path, limits: ParseLimits) -> None:
    size = path.stat().st_size
    if size > limits.max_file_mb * 1024 * 1024:
        raise ParseError(f"file is {size / 1e6:.0f} MB; the limit is {limits.max_file_mb} MB")
    if path.suffix.lower() in ZIP_TYPES:
        check_zip(path, limits)


def check_zip(path: Path, limits: ParseLimits) -> None:
    """Refuse zip bombs by reading the archive's directory (nothing is decompressed)."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ParseError(f"not a valid Office file: {exc}") from exc
    if len(entries) > limits.zip_max_entries:
        raise ParseError(f"archive has {len(entries)} entries; the limit is {limits.zip_max_entries}")
    total = sum(e.file_size for e in entries)
    if total > limits.zip_max_uncompressed_mb * 1024 * 1024:
        raise ParseError(
            f"archive expands to {total / 1e6:.0f} MB; the limit is {limits.zip_max_uncompressed_mb} MB"
        )
    for entry in entries:
        if entry.compress_size and entry.file_size / entry.compress_size > limits.zip_max_ratio:
            raise ParseError(f"suspicious compression ratio in {entry.filename!r} (possible zip bomb)")


# ---- The child process --------------------------------------------------------------------
def _child_env(offline: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(marker in k.upper() for marker in SECRET_MARKERS)}
    if offline:
        env["HF_HUB_OFFLINE"] = "1"  # use cached models; don't try (and time out on) the network
    # OpenBLAS reserves a buffer per thread and defaults to one thread per core: 16 cores put the
    # child at ~9.4 GB of address space, over the parse_memory_mb cap. Four threads stay ~5 GB.
    env.setdefault("OPENBLAS_NUM_THREADS", "4")
    return env


def _limit_resources(limits: ParseLimits) -> None:
    """Runs in the child between fork and exec (preexec_fn), so it binds the parser from its start."""
    if limits.memory_mb:
        size = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (size, size))
    cpu = int(limits.timeout_seconds) + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    written = limits.write_limit_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (written, written))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


@cache
def network_isolation_available() -> bool:
    """Can this system run a command in a new user + network namespace without privileges?"""
    unshare = shutil.which("unshare")
    if not unshare or not sys.platform.startswith("linux"):
        return False
    try:
        probe = subprocess.run(
            [unshare, "--user", "--map-current-user", "--net", "true"], capture_output=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


class IsolatedParser:
    """Parses files out of process. `command` can be overridden (tests)."""

    def __init__(
        self,
        limits: ParseLimits | None = None,
        device: str = "cpu",
        network_isolation: Literal["auto", "on", "off"] = "auto",
        command: list[str] | None = None,
    ):
        self.limits = limits or ParseLimits()
        self.device = device
        if device != "cpu" and self.limits.memory_mb:
            # GPU runtimes (ROCm, CUDA) reserve far more address space than they use, so an
            # address-space cap breaks them. Time and write limits still apply.
            logger.info("parser memory cap disabled on %s (incompatible with GPU runtimes)", device)
            self.limits = replace(self.limits, memory_mb=0)
        self.command = command or [sys.executable, "-m", "app.ingest.parse_child"]
        if network_isolation == "off":
            self.no_network = False
        elif network_isolation_available():
            self.no_network = True
        elif network_isolation == "on":
            raise RuntimeError(
                "PARSE_NETWORK_ISOLATION=on, but unprivileged network namespaces are unavailable "
                "(needs Linux with `unshare` and user namespaces enabled)."
            )
        else:
            self.no_network = False
            logger.warning("parser runs with network access: network namespaces are unavailable here")

    def _argv(self, path: Path) -> list[str]:
        argv = [*self.command, str(path), self.device, str(self.limits.max_pages)]
        if self.no_network:
            argv = [
                shutil.which("unshare") or "unshare",
                "--user",
                "--map-current-user",
                "--net",
                "--",
                *argv,
            ]
        return argv

    async def parse(self, path: Path) -> ParsedDocument:
        preflight(path, self.limits)
        process = await asyncio.create_subprocess_exec(
            *self._argv(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=BASE_DIR,
            env=_child_env(offline=self.no_network),
            start_new_session=True,  # its own process group, so a kill reaches its children too
            preexec_fn=lambda: _limit_resources(self.limits),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.limits.timeout_seconds)
        except TimeoutError:
            _kill_group(process.pid)
            await process.wait()
            raise ParseError(
                f"parsing took longer than {self.limits.timeout_seconds:.0f}s and was stopped"
            ) from None
        except asyncio.CancelledError:
            _kill_group(process.pid)
            raise
        return _decode(stdout, stderr, process.returncode)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _decode(stdout: bytes, stderr: bytes, returncode: int | None) -> ParsedDocument:
    try:
        out = json.loads(stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        tail = stderr.decode("utf-8", errors="replace").strip()[-MAX_ERROR_CHARS:]
        reason = _signal_reason(returncode) or tail or f"exit code {returncode}"
        raise ParseError(f"parser process failed: {reason}") from None
    if "error" in out:
        raise ParseError(str(out["error"])[:MAX_ERROR_CHARS])
    return ParsedDocument(
        title=out["title"],
        mime_type=out["mime_type"],
        sections=[Section(heading, text) for heading, text in out["sections"]],
    )


def _signal_reason(returncode: int | None) -> str:
    if returncode is not None and returncode < 0:
        name = signal.Signals(-returncode).name
        hints = {
            "SIGXCPU": "CPU time limit",
            "SIGKILL": "killed (memory or timeout)",
            "SIGXFSZ": "write limit",
        }
        return f"stopped by {name} ({hints.get(name, 'signal')})"
    return ""
