"""Settings, read from environment variables and `backend/.env` (see `.env.example`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent

DEV_SECRETS = {"vault-dev-password", "vault-dev-openfga-key", "vault-dev-qdrant-key"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # ---- Runtime ----------------------------------------------------------------------
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    # Origin the browser uses; also the only origin allowed by CORS.
    public_origin: str = "http://localhost:5174"

    # ---- Postgres (system of record) --------------------------------------------------
    database_url: str = "postgresql+asyncpg://vault:vault-dev-password@127.0.0.1:5433/vault"

    # ---- Authorization ----------------------------------------------------------------
    # openfga: the OpenFGA service decides (production). sql: the same rules evaluated from
    # Postgres tables (no extra service; also the test oracle for OpenFGA).
    authz_backend: Literal["openfga", "sql"] = "openfga"
    openfga_url: str = "http://127.0.0.1:8081"
    openfga_api_key: str = "vault-dev-openfga-key"
    openfga_store_name: str = "sentinel-vault"
    # While OpenFGA lags behind Postgres (a failed sync), queries use the SQL rules and the
    # sync is retried this often.
    authz_sync_retry_seconds: float = 5.0

    # ---- Vector store -----------------------------------------------------------------
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = "vault-dev-qdrant-key"
    qdrant_collection: str = "chunks"

    # ---- Models (Ollama) --------------------------------------------------------------
    ollama_url: str = "http://127.0.0.1:11434"
    # Answer model. Laptop: qwen3.5:4b. RX 9070 XT desktop: qwen3.5:9b or gpt-oss:20b.
    llm_model: str = "qwen3.5:4b"
    # Must be identical on every machine that shares an index.
    embedding_model: str = "qwen3-embedding:0.6b"
    # "ollama" in normal use; "hash" is a deterministic offline stand-in used by the tests.
    embedding_backend: Literal["ollama", "hash"] = "ollama"
    llm_backend: Literal["ollama", "extractive"] = "ollama"
    llm_timeout_seconds: float = 180.0
    # Context window for answers. 6 passages of ~400 tokens fit in 4096; larger windows cost RAM
    # (KV cache), which matters on a 16 GB laptop. The RX 9070 XT desktop can use 16384.
    llm_context_tokens: int = 4096
    # How long Ollama keeps the answer model loaded after a request.
    llm_keep_alive: str = "2m"
    # Mark retrieved text so instructions inside documents read as data (Spotlighting).
    datamark_sources: bool = True

    # ---- Prompt gate (Laya, from Sentinel) --------------------------------------------
    gate_engine: Literal["laya", "heuristic", "off"] = "laya"
    # PyTorch device for Laya and Docling: cpu (laptop) | rocm (RX 9070 XT, needs the rocm
    # dependency group) | cuda. Ollama chooses its own GPU independently.
    torch_device: Literal["cpu", "rocm", "cuda"] = "cpu"
    gate_injection_threshold: float = 0.5
    gate_exfiltration_threshold: float = 0.6
    # Laya's multilingual checkpoint (Hindi and other non-Latin scripts) scores ordinary text
    # higher, so it gets its own thresholds, fitted with `app.cli gate-eval` to wrongly block at
    # most ~2% of ordinary Hindi questions. That lets more attacks through (see README).
    gate_multilingual_injection_threshold: float = 0.93
    gate_multilingual_exfiltration_threshold: float = 0.61
    # Allowed prompts scoring at least this on either question are logged as `gate.review`.
    gate_review_threshold: float | None = 0.5
    # Second look by the local LLM (translate for Laya's english checkpoint + a direct judge) at
    # Hindi prompts in the review band ("review") or every Hindi prompt ("all"), and at Latin text
    # that isn't recognisably English (Hinglish). It can only add blocks. Needs GATE_ENGINE=laya.
    gate_second_stage: Literal["off", "review", "all"] = "review"
    gate_second_stage_model: str = ""  # default: LLM_MODEL
    gate_second_stage_timeout_seconds: float = 15.0

    # ---- Library and retrieval --------------------------------------------------------
    library_dir: Path = REPO_DIR / "data" / "library"
    max_upload_mb: int = 50
    chunk_max_tokens: int = 400
    retrieval_candidates: int = 30  # per search leg (dense and full-text)
    retrieval_top_k: int = 6  # chunks given to the model
    # Reorder the fused candidates with a cross-encoder before taking the top k (rerank.py).
    # cross-encoder: MiniLM-L6, fast on CPU (laptop). qwen: Qwen3-Reranker-0.6B, for a GPU.
    reranker: Literal["off", "cross-encoder", "qwen"] = "cross-encoder"
    reranker_model: str = ""  # empty: the default model for the kind above
    rerank_candidates: int = 12  # how many fused candidates are rescored

    # ---- Sessions ---------------------------------------------------------------------
    session_hours: int = 8
    session_idle_minutes: int = 60
    cookie_secure: bool = False  # True behind HTTPS

    # ---- Sign-in throttling -------------------------------------------------------------
    # After this many failed sign-ins within the window, the account is locked for the
    # lockout period (attempts are refused without checking the password).
    login_max_failures: int = 5
    # The same, per client address, to slow password spraying across many accounts.
    login_ip_max_failures: int = 30
    login_window_minutes: int = 15
    login_lockout_minutes: int = 15
    # Take the client address from X-Forwarded-For. Only enable behind a reverse proxy that
    # sets it (otherwise clients could forge it). The Vite dev proxy sets it.
    trust_proxy_headers: bool = False

    # ---- Background indexing ----------------------------------------------------------
    run_worker: bool = True  # index documents inside the API process

    # ---- Parsing untrusted files ----------------------------------------------------------
    # Every file is parsed in a separate, killable process with these limits (app/ingest/sandbox.py).
    parse_timeout_seconds: float = 300.0
    parse_memory_mb: int = 6144  # address-space limit for the parser process; 0 = none
    parse_max_file_mb: int = 200
    parse_max_pages: int = 500
    # Office files are zip archives: refuse zip bombs before opening them.
    zip_max_uncompressed_mb: int = 500
    zip_max_ratio: int = 100
    zip_max_entries: int = 10000
    # Run the parser with no network (a Linux network namespace via `unshare`). auto: use it
    # when the system allows; on: refuse to parse without it; off: never.
    parse_network_isolation: Literal["auto", "on", "off"] = "auto"
    scan_on_startup: bool = True

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env != "production":
            return self
        if any(
            secret in s
            for s in (self.database_url, self.openfga_api_key, self.qdrant_api_key)
            for secret in DEV_SECRETS
        ):
            raise ValueError("Production refuses the development passwords and API keys.")
        if not self.cookie_secure:
            raise ValueError("Production requires COOKIE_SECURE=true (serve over HTTPS).")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
