#!/bin/bash
# gpu_score_runner_b3_validate.sh — B3 validation runner (5 sessions, gemma3:27b).
#
# Purpose: score the 5 hardcoded validation sessions and keep VM alive for review.
# After inspection, trigger the full run manually:
#   bash /opt/scoring/repo/scripts/gpu_score_runner_b3_full.sh 2>&1 | tee /var/log/scoring.log
#
# Shutdown strategy:
#   Hard watchdog: background sleep+poweroff fires 4h after this script starts.
#   NO EXIT trap — VM stays up after validation so verdicts can be reviewed via SSH.
#
# Usage: bash /opt/scoring/repo/scripts/gpu_score_runner_b3_validate.sh 2>&1 | tee /var/log/validate.log

set -euo pipefail

REPO_DIR="/opt/scoring/repo"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Hard watchdog: VM shuts down 4h after this script starts (safety net only).
# Much longer than needed for 5 sessions; keeps VM alive for review.
# ---------------------------------------------------------------------------
(sleep 14400 && echo "[$(date)] Hard watchdog: 4h elapsed, forcing poweroff." && sudo poweroff) &
disown

# NO EXIT trap here — we want the VM to remain running after validation completes.

echo "[$(date)] === B3 VALIDATION RUNNER (5 sessions, gemma3:27b) ==="
WALL_START=$(date +%s)

# ---------------------------------------------------------------------------
# Pre-run checks
# ---------------------------------------------------------------------------
echo "[$(date)] GPU info:"
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv

echo "[$(date)] Loaded models:"
ollama list

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
# Validation: 5 sessions
# ---------------------------------------------------------------------------
echo "[$(date)] Starting validation (5 sessions)..."

python3 scripts/second_judge_run.py \
    --mode validate \
    --model gemma3:27b \
    --ollama-url http://localhost:11434

WALL_END=$(date +%s)
WALL_ELAPSED=$((WALL_END - WALL_START))

echo "[$(date)] === VALIDATION COMPLETE (${WALL_ELAPSED}s wall time) ==="
echo "[$(date)] Review verdicts above."
echo "[$(date)] When satisfied, trigger the full run:"
echo "  bash /opt/scoring/repo/scripts/gpu_score_runner_b3_full.sh 2>&1 | tee /var/log/scoring.log"
echo "[$(date)] VM will remain running until the hard watchdog fires (4h from script start)"
echo "          or you run: sudo poweroff"
