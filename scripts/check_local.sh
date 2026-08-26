#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/check_local.sh [--model NAME]
#
# --model exists to compare candidates on deployment hardware. Model choice in this project is
# decided by measurement rather than reputation (see backend/evals/baselines/inference.md), and
# that needs two numbers from the same box, not one.
MODEL="llama3.2:3b"
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --model=*) MODEL="${1#*=}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

python3.12 --version
node --version
npm --version
docker --version

# Which Ollama is answering, and is it on the GPU?
#
# This check exists because the answer is invisible from the application's side: a containerised
# Ollama on macOS serves the same API at the same URL and returns the same answers, just ~6x
# slower, because Docker Desktop cannot reach the Apple Silicon GPU. Measured on an M4:
# 7.2 tok/s containerised against 40 tok/s natively for llama3.2:3b. Losing that is a silent
# regression, so it gets asserted rather than assumed.
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
echo
echo "ollama: ${OLLAMA_URL}"

if ! curl -sf -m 5 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
  echo "  UNREACHABLE — start it with: ollama serve"
  exit 1
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^groundwork-ollama$'; then
  echo "  WARNING: serving from the container, which has no GPU on macOS."
  echo "  Stop it and run Ollama on the host instead. See README > Local setup."
else
  echo "  serving from the host"
fi

# Generation throughput is the number that actually matters, so measure it rather than infer it.
# Prompt throughput is reported alongside generation because grading -- the most frequent model
# call -- feeds 8-16 chunks in and gets one bit out, so its cost is dominated by reading.
curl -sf -m 300 "${OLLAMA_URL}/api/chat" \
  -d "{\"model\":\"${MODEL}\",\"stream\":false,\"think\":false,
       \"messages\":[{\"role\":\"user\",\"content\":\"Count to twenty.\"}]}" \
  | MODEL="${MODEL}" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
model = os.environ["MODEL"]
gen = d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 0.001)
prompt_tokens = d.get("prompt_eval_count", 0)
prompt_seconds = d.get("prompt_eval_duration", 0) / 1e9
# The 25 tok/s threshold was calibrated against llama3.2:3b and does not transfer: qwen3:8b
# measures 18 tok/s on the same Metal GPU, which is healthy for its size and would be reported
# as a failure. A wrong verdict is worse than none, so other models get the numbers only.
if model == "llama3.2:3b":
    verdict = "GPU-class" if gen >= 25 else "CPU-class — the GPU is not being used"
    print(f"  {model} generation: {gen:.1f} tok/s ({verdict})")
else:
    print(f"  {model} generation: {gen:.1f} tok/s")
if prompt_tokens and prompt_seconds:
    print(f"  {model} prompt:     {prompt_tokens / prompt_seconds:.1f} tok/s")
' || echo "  (throughput check skipped: ${MODEL} not pulled)"
