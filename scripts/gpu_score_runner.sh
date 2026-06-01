#!/bin/bash
# gpu_score_runner.sh — Qwen3-30b judge scoring runner for GCP spot VM (L4 GPU, us-central1)
#
# Shutdown strategy:
#   Belt 1 (watchdog): background sleep+poweroff fires if Python hangs past 2h
#   Belt 2 (EXIT trap): cleanup() fires on normal exit, error exit, or signal
#
# Usage: bash /opt/scoring/repo/scripts/gpu_score_runner.sh
# Env:   GITHUB_TOKEN — if set, pushes judge_scores.jsonl to GitHub; if unset, prints scp command
#
# IMPORTANT: uses only local Ollama (http://localhost:11434). No Anthropic API.

set -euo pipefail

REPO_DIR="/opt/scoring/repo"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Belt 1 — Internal watchdog (2h hard kill, in case Python hangs)
# ---------------------------------------------------------------------------
(sleep 7200 && echo "[$(date)] Watchdog: 2h elapsed, forcing poweroff." && sudo poweroff) &
disown

# ---------------------------------------------------------------------------
# Belt 2 — EXIT trap (fires on normal exit, error exit, or signal)
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
echo "[$(date)] === GPU scoring runner starting ==="
echo "[$(date)] GPU info:"
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv

echo "[$(date)] Ollama models available:"
ollama list

# ---------------------------------------------------------------------------
# Clear JSONL — truncate data/judge_scores.jsonl, rescore all 69 fresh
# ---------------------------------------------------------------------------
echo "[$(date)] Clearing data/judge_scores.jsonl..."
python3 - <<'EOF'
lines_before = 0
path = "data/judge_scores.jsonl"
try:
    with open(path, "r") as f:
        lines_before = sum(1 for line in f if line.strip())
except FileNotFoundError:
    lines_before = 0

with open(path, "w") as f:
    pass  # truncate

print(f"[clear] Cleared {lines_before} lines from {path}")
EOF

# ---------------------------------------------------------------------------
# Score 69 sessions via layer2_judge.py
# ---------------------------------------------------------------------------
echo "[$(date)] Starting layer2_judge.py for 69 sessions..."
python3 scripts/layer2_judge.py \
    --model qwen3:30b-a3b \
    --session-ids-file data/calibration_sample.json \
    --ollama-url http://localhost:11434

echo "[$(date)] Scoring complete."

# ---------------------------------------------------------------------------
# Print verdict distribution
# ---------------------------------------------------------------------------
echo "[$(date)] Computing verdict distribution..."
python3 - <<'EOF'
import json
from collections import Counter

VALID_VERDICTS = {"MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"}

path = "data/judge_scores.jsonl"
rows = []
parse_errors = 0

with open(path, "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1

total = len(rows)
verdicts = [r.get("verdict", "") for r in rows]
counts = Counter(verdicts)
valid_count = sum(counts.get(v, 0) for v in VALID_VERDICTS)
parse_rate = (valid_count / total * 100) if total > 0 else 0.0

print(f"[distribution] Total rows: {total}")
print(f"[distribution] Verdict counts: {dict(counts)}")
print(f"[distribution] Parse success rate: {parse_rate:.1f}% ({valid_count}/{total} rows have valid verdict)")
if parse_errors:
    print(f"[distribution] WARNING: {parse_errors} lines failed JSON parse")
EOF

# ---------------------------------------------------------------------------
# Push to GitHub (or print manual scp fallback)
# ---------------------------------------------------------------------------
echo "[$(date)] Checking GITHUB_TOKEN..."
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "[$(date)] WARNING: GITHUB_TOKEN is not set. Skipping git push."
    echo "[$(date)] To retrieve results manually, run from your local machine:"
    echo "    gcloud compute scp qwen3-judge-gpu:/opt/scoring/repo/data/judge_scores.jsonl data/judge_scores.jsonl --zone=us-central1-a --project=aetherart-497918"
else
    echo "[$(date)] GITHUB_TOKEN set. Pushing judge_scores.jsonl to GitHub..."
    git config user.email "gaurav.gandhi2411@gmail.com"
    git config user.name "gaurav-gandhi-2411"
    git add data/judge_scores.jsonl
    git commit -m "feat(data): 69-session GPU calibration scores (qwen3:30b-a3b v3 us-central1 L4)"
    git push "https://${GITHUB_TOKEN}@github.com/gaurav-gandhi-2411/token-efficiency-scorer.git" master
    echo "[$(date)] Push complete."
fi

# ---------------------------------------------------------------------------
# Final — fall through to EXIT trap (shutdown)
# ---------------------------------------------------------------------------
echo "[$(date)] Done."
sleep 5
# EXIT trap fires here, which calls sudo shutdown -h now
