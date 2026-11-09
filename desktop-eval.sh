#!/usr/bin/env bash
# One-off: bring up Sentinel Vault on the desktop with ROCm, download models, run both test
# suites and the full eval, put the answer table in the README, then commit and push.
# Run from the repo root:  bash desktop-eval.sh
# Stops at the first failure. Logs go to ./desktop-eval-logs/.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$PWD
LOGS=$ROOT/desktop-eval-logs
mkdir -p "$LOGS"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

# ---- 1. preflight + .env + start ----------------------------------------------------------
say "ROCm preflight"
[[ -e /dev/kfd ]] || die "/dev/kfd is missing: the amdgpu kernel driver isn't loaded"
GROUPS_NOW=" $(id -nG) "
for g in render video; do
  [[ "$GROUPS_NOW" == *" $g "* ]] || die "you're not in the '$g' group: sudo usermod -aG render,video $USER, then log out and back in"
done
say "/dev/kfd present; in render and video groups"

if [[ -f backend/.env ]]; then
  cp backend/.env "$LOGS/env.backup"
  say "backend/.env existed; backed it up to $LOGS/env.backup before overwriting"
fi
cp backend/.env.desktop.example backend/.env
# run.sh pulls the Ollama models named in the environment, not in backend/.env; pass them through.
export LLM_MODEL=$(sed -n 's/^LLM_MODEL=//p' backend/.env)
export EMBEDDING_MODEL=$(sed -n 's/^EMBEDDING_MODEL=//p' backend/.env)
say "Ollama models: $LLM_MODEL, $EMBEDDING_MODEL"
for tool in docker uv ollama pnpm; do command -v "$tool" >/dev/null || die "$tool isn't installed or isn't on PATH"; done
docker info >/dev/null 2>&1 || die "docker is installed but this user can't reach the daemon (docker group? service running?)"

say "Starting ./run.sh --gpu rocm (log: $LOGS/run.log). First run syncs ~9 GB of ROCm PyTorch."
# setsid: run.sh's cleanup does `kill 0` on its own process group; keep that away from this script.
setsid ./run.sh --gpu rocm >"$LOGS/run.log" 2>&1 &
RUN_PID=$!
stop_stack() { say "Stopping the API and UI"; kill -TERM -- "-$RUN_PID" 2>/dev/null || true; }
trap stop_stack EXIT

for i in $(seq 1 2400); do   # up to 40 minutes (the PyTorch download dominates)
  curl -sf http://127.0.0.1:8010/api/health >/dev/null && break
  kill -0 "$RUN_PID" 2>/dev/null || { tail -40 "$LOGS/run.log"; die "run.sh exited before the API came up"; }
  (( i % 60 == 0 )) && say "still waiting for the API ($((i/60)) min)… last log line: $(tail -1 "$LOGS/run.log")"
  sleep 1
done
curl -sf http://127.0.0.1:8010/api/health >/dev/null || die "API not healthy after 40 minutes (see $LOGS/run.log)"
say "API is up"

cd backend
uv run --no-sync python -c "import torch; print('torch', torch.__version__, 'hip', torch.version.hip, 'gpu', torch.cuda.is_available() and torch.cuda.get_device_name(0))" \
  | tee "$LOGS/torch.txt"
grep -q "hip None" "$LOGS/torch.txt" && die "PyTorch isn't the ROCm build (torch.version.hip is None)"

# ---- 2. models ----------------------------------------------------------------------------
say "download-docling";  uv run --no-sync python -m app.cli download-docling  2>&1 | tee "$LOGS/download-docling.log"
say "download-reranker"; uv run --no-sync python -m app.cli download-reranker 2>&1 | tee "$LOGS/download-reranker.log"

# ---- 3. tests -----------------------------------------------------------------------------
say "pytest (default authz)";   uv run --no-sync pytest -q 2>&1 | tee "$LOGS/pytest.log"
say "pytest (TEST_AUTHZ=sql)";  TEST_AUTHZ=sql uv run --no-sync pytest -q 2>&1 | tee "$LOGS/pytest-sql.log"

# ---- 4. eval ------------------------------------------------------------------------------
say "eval: off, cross-encoder, qwen + answers (log: $LOGS/eval.log)"
uv run --no-sync python -m app.cli eval --rerankers off,cross-encoder,qwen --answers 2>&1 | tee "$LOGS/eval.log"
REPORT=$(sed -n 's/^full report: //p' "$LOGS/eval.log" | tail -1)
[[ -f "$REPORT" ]] || die "couldn't find the eval report path in $LOGS/eval.log"
say "report: $REPORT"
cd "$ROOT"

# ---- 5. README + commit + push ------------------------------------------------------------
say "Updating the README's Evaluation section"
python3 - "$REPORT" README.md <<'PY'
import json, sys, pathlib
report = json.loads(pathlib.Path(sys.argv[1]).read_text())
readme = pathlib.Path(sys.argv[2])
text = readme.read_text()

old_start = "Answer scores (`--answers`: facts present, correct document cited, correct refusals) still need a"
old_end = "On the 16 GB laptop, the run alongside the API ran out of memory.\n"
i = text.find(old_start)
j = text.find(old_end, i)
if i < 0 or j < 0:
    sys.exit("README: the 'still needs a run on the desktop' note wasn't found; edit it by hand")

configs = {k: v for k, v in report["configs"].items() if "summary" in v}
if not configs:
    sys.exit("eval report has no completed configurations")
cols = list(next(iter(configs.values()))["summary"])
labels = {"off": "none (RRF only)", "cross-encoder": "`cross-encoder` (MiniLM-L6)", "qwen": "`qwen` (Qwen3-Reranker-0.6B)"}
rows = ["| Reranker | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
for name, c in configs.items():
    rows.append(f"| {labels.get(name, name)} | " + " | ".join(str(c["summary"][k]) for k in cols) + " |")

new = (
    "Answers on the desktop (Ryzen 7 9800X3D, RX 9070 XT with ROCm, `qwen3.5:9b`, "
    f"top {report['top_k']} passages, {report['questions']} questions; `--answers` scores facts "
    "present, correct document cited and correct refusals):\n\n" + "\n".join(rows) + "\n"
)
readme.write_text(text[:i] + new + text[j + len(old_end):])
print(new)
PY

git --no-pager diff --stat README.md
git add README.md
git commit -m "README: answer-quality eval results from the desktop (ROCm)"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
say "Pushing $BRANCH to origin"
git push origin "$BRANCH"
say "Done. Logs and the full eval report are in $LOGS and backend/eval/results."
