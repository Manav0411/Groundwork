#!/usr/bin/env bash
set -euo pipefail

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
curl -sf -m 120 "${OLLAMA_URL}/api/chat" \
  -d '{"model":"llama3.2:3b","stream":false,"think":false,
       "messages":[{"role":"user","content":"Count to twenty."}]}' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
tokens = d.get("eval_count", 0)
seconds = d.get("eval_duration", 1) / 1e9
rate = tokens / max(seconds, 0.001)
verdict = "GPU-class" if rate >= 25 else "CPU-class — the GPU is not being used"
print(f"  llama3.2:3b generation: {rate:.1f} tok/s ({verdict})")
' || echo "  (throughput check skipped: llama3.2:3b not pulled)"
