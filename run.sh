#!/usr/bin/env bash
# Sentinel Vault: start everything for local use.
#   ./run.sh              services (Docker), migrations, demo data, API on :8010, UI on :5174
#   ./run.sh --gpu rocm   the same on an AMD GPU (RX 9070 XT): installs the ROCm PyTorch build
#                         for Laya and Docling and sets TORCH_DEVICE=rocm (Linux only)
#   ./run.sh --no-demo    skip the demo users and grants
# Needs: Docker, uv, Node 20+ with pnpm, and Ollama with the two models (see README).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

SEED=1
GPU=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-demo) SEED=0 ;;
    --gpu) GPU="${2:-}"; shift ;;
    *) die "unknown option $1 (see the header of run.sh)" ;;
  esac
  shift
done
# A GPU configured in backend/.env is honoured too, so a plain ./run.sh doesn't swap PyTorch back to CPU.
if [[ -z "$GPU" && -f backend/.env ]] && grep -qE '^TORCH_DEVICE=rocm' backend/.env; then GPU=rocm; fi

SYNC_FLAGS=()
if [[ "$GPU" == "rocm" ]]; then
  [[ "$(uname -s)" == "Linux" ]] || die "--gpu rocm needs Linux (PyTorch publishes ROCm builds for Linux only)"
  [[ -e /dev/kfd ]] || die "/dev/kfd is missing: the amdgpu kernel driver isn't loaded"
  SYNC_FLAGS=(--no-group cpu --group rocm)
  export TORCH_DEVICE=rocm
  say "GPU mode: AMD ROCm"
elif [[ -n "$GPU" ]]; then
  die "--gpu accepts only rocm"
fi

say "Starting Postgres, OpenFGA and Qdrant"
docker compose up -d --wait postgres openfga qdrant

# The models the API will use: the shell's value if set, else backend/.env, else the defaults.
env_value() {
  [[ -f backend/.env ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" backend/.env | tail -1 \
    | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//; s/^["'"'"'](.*)["'"'"']$/\1/'
}
LLM_MODEL="${LLM_MODEL:-$(env_value LLM_MODEL)}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-$(env_value EMBEDDING_MODEL)}"

say "Checking Ollama models"
for model in "${LLM_MODEL:-qwen3.5:4b}" "${EMBEDDING_MODEL:-qwen3-embedding:0.6b}"; do
  ollama list | awk '{print $1}' | grep -qx "$model" || { say "Pulling $model"; ollama pull "$model"; }
done

say "Backend dependencies and migrations"
(cd backend && uv sync "${SYNC_FLAGS[@]}" && uv run --no-sync alembic upgrade head)
if [[ $SEED == 1 ]]; then
  say "Demo users and grants (idempotent)"
  (cd backend && uv run --no-sync python -m app.cli seed-demo)
fi

say "Frontend dependencies"
(cd frontend && pnpm install --frozen-lockfile)

cleanup() { say "Stopping"; kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

say "API on http://127.0.0.1:8010"
# --no-sync: plain `uv run` would re-sync the default (CPU) PyTorch over the ROCm build.
(cd backend && uv run --no-sync uvicorn app.main:app --host 127.0.0.1 --port 8010) &
for _ in $(seq 1 90); do curl -sf http://127.0.0.1:8010/api/health >/dev/null && break; sleep 1; done

say "UI on http://localhost:5174 (demo password: vault-demo-2026)"
(cd frontend && pnpm exec vite --host 127.0.0.1) &
wait
