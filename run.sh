#!/usr/bin/env bash
# Sentinel Vault: start everything for local use.
#   ./run.sh            services (Docker), migrations, demo data, API on :8010, UI on :5174
#   ./run.sh --no-demo  skip the demo users and grants
# Needs: Docker, uv, Node 20+ with pnpm, and Ollama with the two models (see README).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

SEED=1
[[ "${1:-}" == "--no-demo" ]] && SEED=0

say "Starting Postgres, OpenFGA and Qdrant"
docker compose up -d --wait postgres openfga qdrant

say "Checking Ollama models"
for model in "${LLM_MODEL:-qwen3.5:4b}" "${EMBEDDING_MODEL:-qwen3-embedding:0.6b}"; do
  ollama list | awk '{print $1}' | grep -qx "$model" || { say "Pulling $model"; ollama pull "$model"; }
done

say "Backend dependencies and migrations"
(cd backend && uv sync && uv run alembic upgrade head)
if [[ $SEED == 1 ]]; then
  say "Demo users and grants (idempotent)"
  (cd backend && uv run python -m app.cli seed-demo)
fi

say "Frontend dependencies"
(cd frontend && pnpm install --frozen-lockfile)

cleanup() { say "Stopping"; kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

say "API on http://127.0.0.1:8010"
(cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8010) &
for _ in $(seq 1 90); do curl -sf http://127.0.0.1:8010/api/health >/dev/null && break; sleep 1; done

say "UI on http://localhost:5174 (demo password: vault-demo-2026)"
(cd frontend && pnpm exec vite --host 127.0.0.1) &
wait
