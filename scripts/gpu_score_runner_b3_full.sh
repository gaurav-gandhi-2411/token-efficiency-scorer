#!/bin/bash
# gpu_score_runner_b3_full.sh — B3 full pool scoring runner (143 sessions, gemma3:27b).
#
# Input:  data/corpus_pool/pool_adapted.jsonl  (143 sessions, all <=551 turns)
#         data/pool_judge_scores.jsonl          (Qwen-scored set, 143 sessions)
# Output: data/pool_judge_scores_m2.jsonl
#
# Run AFTER gpu_score_runner_b3_validate.sh verdicts have been reviewed.
#
# Shutdown strategy:
#   Belt 1 (watchdog): background sleep+poweroff fires if Python hangs past 3h
#   Belt 2 (EXIT trap): cleanup() fires on normal exit, error exit, or signal
#
# Usage: bash /opt/scoring/repo/scripts/gpu_score_runner_b3_full.sh 2>&1 | tee /var/log/scoring.log
# Env:   GITHUB_TOKEN — if set, pushes pool_judge_scores_m2.jsonl to GitHub

set -euo pipefail

REPO_DIR="/opt/scoring/repo"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Belt 1 — Internal watchdog (3h hard kill)
# ---------------------------------------------------------------------------
(sleep 10800 && echo "[$(date)] Watchdog: 3h elapsed, forcing poweroff." && sudo poweroff) &
disown

# ---------------------------------------------------------------------------
# Belt 2 — EXIT trap
# ---------------------------------------------------------------------------
cleanup() {
    local exit_code=$?
    echo "[$(date)] EXIT trap fired with code ${exit_code}. Shutting down VM."
    sudo shutdown -h now
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-run checks
# ---------------------------------------------------------------------------
echo "[$(date)] === B3 full pool scoring runner starting (gemma3:27b) ==="
WALL_START=$(date +%s)

echo "[$(date)] GPU info:"
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv

echo "[$(date)] Ollama GPU residency check:"
curl -s http://localhost:11434/api/ps | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in data.get('models', []):
    total = m.get('size', 0)
    vram  = m.get('size_vram', 0)
    pct   = vram / total * 100 if total else 0
    print(f'  {m[\"name\"]}: {vram/1e9:.1f} GB VRAM / {total/1e9:.1f} GB total ({pct:.0f}% GPU-resident)')
" || echo "  (ps endpoint unavailable)"

# ---------------------------------------------------------------------------
# Preemption-safe sentinel (different name from B2 to avoid collision)
# ---------------------------------------------------------------------------
CLEARED_MARKER="$REPO_DIR/data/.b3_scores_cleared"

if [ ! -f "$CLEARED_MARKER" ]; then
    echo "[$(date)] First run: clearing data/pool_judge_scores_m2.jsonl..."
    python3 - <<'EOF'
path = "data/pool_judge_scores_m2.jsonl"
try:
    with open(path, "r") as f:
        lines_before = sum(1 for line in f if line.strip())
except FileNotFoundError:
    lines_before = 0
with open(path, "w") as f:
    pass
print(f"[clear] Cleared {lines_before} prior rows from {path}")
EOF
    touch "$CLEARED_MARKER"
    echo "[$(date)] Sentinel written: $CLEARED_MARKER"
else
    echo "[$(date)] RESUME MODE: sentinel found — skipping truncate."
fi

# ---------------------------------------------------------------------------
# Single-session timing probe (confirms GPU speed before 143-session run)
# ---------------------------------------------------------------------------
echo "[$(date)] Timing probe: scoring first session to confirm GPU speed..."

PROBE_SESSION=$(python3 -c "
import json
with open('data/corpus_pool/pool_adapted.jsonl') as f:
    first = json.loads(f.readline())
print(first['session_id'])
")

echo "[$(date)] Probe session_id: $PROBE_SESSION"
PROBE_START=$(date +%s)
python3 scripts/second_judge_run.py \
    --mode validate \
    --model gemma3:27b \
    --ollama-url http://localhost:11434 \
    --force
PROBE_END=$(date +%s)
PROBE_ELAPSED=$((PROBE_END - PROBE_START))
echo "[$(date)] Probe done in ${PROBE_ELAPSED}s."

if [ "$PROBE_ELAPSED" -gt 600 ]; then
    echo "[$(date)] ABORT: probe took ${PROBE_ELAPSED}s (>10 min). GPU not viable. Stopping."
    exit 1   # EXIT trap fires, VM shuts down
fi
if [ "$PROBE_ELAPSED" -gt 300 ]; then
    echo "[$(date)] WARNING: probe took ${PROBE_ELAPSED}s — GPU may not be fully resident."
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv
fi
echo "[$(date)] Probe OK: ${PROBE_ELAPSED}s. Proceeding with full 143-session run."

# ---------------------------------------------------------------------------
# Full run: 143 sessions (skip-if-scored resumes on preemption restart)
# ---------------------------------------------------------------------------
echo "[$(date)] Starting full B3 pool run (143 sessions, gemma3:27b)..."
python3 scripts/second_judge_run.py \
    --mode run \
    --model gemma3:27b \
    --ollama-url http://localhost:11434

echo "[$(date)] Scoring complete."

WALL_END=$(date +%s)
WALL_ELAPSED=$((WALL_END - WALL_START))
echo "[$(date)] Total wall time: ${WALL_ELAPSED}s"

# ---------------------------------------------------------------------------
# Push to GitHub or print manual scp
# ---------------------------------------------------------------------------
echo "[$(date)] Checking GITHUB_TOKEN..."
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "[$(date)] WARNING: GITHUB_TOKEN not set."
    echo "  Retrieve manually:"
    echo "  gcloud compute scp tes-b3-gemma-run-tmp:/opt/scoring/repo/data/pool_judge_scores_m2.jsonl data/pool_judge_scores_m2.jsonl --zone=asia-east1-a --project=aetherart-497918"
else
    git config user.email "gaurav.gandhi2411@gmail.com"
    git config user.name "gaurav-gandhi-2411"
    git add data/pool_judge_scores_m2.jsonl
    git commit -m "feat(data): B3 Gemma3-27B judge scores (pool, 143 sessions)"
    git push "https://${GITHUB_TOKEN}@github.com/gaurav-gandhi-2411/token-efficiency-scorer.git" master 2>&1 \
        | sed "s/${GITHUB_TOKEN}/***REDACTED***/g" \
        || echo "[$(date)] Push FAILED — retrieve manually via gcloud compute scp"
    echo "[$(date)] Push step done."
fi

echo "[$(date)] Done."
sleep 5
