#!/bin/bash
# cc_score_runner.sh — Judge scoring runner for real CC session validation (3 sessions).
#
# Input:  data/cc_session_digests.jsonl  (4 adapted sessions; session filter skips the
#         3-turn no-AI session d060ce7f)
# Output: data/cc_judge_scores.jsonl
#
# Shutdown strategy:
#   Belt 1 (watchdog): background sleep+poweroff fires if Python hangs past 2h
#   Belt 2 (EXIT trap): cleanup() fires on normal exit, error exit, or signal
#
# Usage: bash /opt/scoring/repo/scripts/cc_score_runner.sh
# Env:   GITHUB_TOKEN — if set, pushes cc_judge_scores.jsonl to GitHub

set -euo pipefail

REPO_DIR="/opt/scoring/repo"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Belt 1 — Internal watchdog (2h hard kill)
# ---------------------------------------------------------------------------
(sleep 7200 && echo "[$(date)] Watchdog: 2h elapsed, forcing poweroff." && sudo poweroff) &
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
echo "[$(date)] === CC validation scoring runner starting ==="
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

echo "[$(date)] Input sessions:"
python3 -c "
import json
sessions = json.load(open('data/cc_validation_sessions.json'))
digests  = [json.loads(l) for l in open('data/cc_session_digests.jsonl')]
digest_map = {r['session_id']: r for r in digests}
for s in sessions:
    sid = s['session_id']
    d = digest_map.get(sid, {})
    print(f'  {sid[:8]}  turns={d.get(\"turn_count\",\"?\"):>4}  tokens={d.get(\"total_tokens\",\"?\"):>10,}')
"

# ---------------------------------------------------------------------------
# Preemption-safe sentinel
# ---------------------------------------------------------------------------
CLEARED_MARKER="$REPO_DIR/data/.cc_scores_cleared"

if [ ! -f "$CLEARED_MARKER" ]; then
    echo "[$(date)] First run: clearing data/cc_judge_scores.jsonl..."
    python3 - <<'EOF'
path = "data/cc_judge_scores.jsonl"
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
# Single-session timing probe (sanity check before full run)
# ---------------------------------------------------------------------------
echo "[$(date)] Timing probe: scoring first session to confirm GPU speed..."
PROBE_START=$(date +%s)
python3 scripts/layer2_judge.py \
    --model qwen3:30b-a3b \
    --input-path data/cc_session_digests.jsonl \
    --output-path data/cc_judge_scores.jsonl \
    --session-ids-file data/cc_validation_sessions.json \
    --mode session \
    --session-id d57f0f0e-56aa-4d3a-9637-98719c8dfe47 \
    --force \
    --ollama-url http://localhost:11434
PROBE_END=$(date +%s)
PROBE_ELAPSED=$((PROBE_END - PROBE_START))
echo "[$(date)] Probe done in ${PROBE_ELAPSED}s."
if [ "$PROBE_ELAPSED" -gt 300 ]; then
    echo "[$(date)] WARNING: probe took ${PROBE_ELAPSED}s (>5 min). GPU may not be fully resident."
    echo "[$(date)] Checking VRAM again:"
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv
fi

# ---------------------------------------------------------------------------
# Full run: 3 sessions (18 / 103 / 413 turns)
# ---------------------------------------------------------------------------
echo "[$(date)] Starting full CC validation run (3 sessions)..."
python3 scripts/layer2_judge.py \
    --model qwen3:30b-a3b \
    --input-path data/cc_session_digests.jsonl \
    --output-path data/cc_judge_scores.jsonl \
    --session-ids-file data/cc_validation_sessions.json \
    --ollama-url http://localhost:11434

echo "[$(date)] Scoring complete."

# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------
python3 - <<'EOF'
import json

path = "data/cc_judge_scores.jsonl"
rows = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

VERDICT_SCORE = {"MUCH_BETTER": 1.0, "BETTER": 0.75, "SIMILAR": 0.5, "WORSE": 0.25, "MUCH_WORSE": 0.0}

print(f"\n{'='*70}")
print(f"CC VALIDATION RESULTS — {len(rows)} sessions scored")
print(f"{'='*70}")
for r in rows:
    sid    = r['session_id']
    v      = r['verdict']
    conf   = r['confidence']
    waste  = r['waste_categories']
    reason = r['reasoning']
    print(f"\n  session: {sid[:8]}")
    print(f"  verdict: {v}  (confidence {conf:.2f})")
    print(f"  waste:   {waste}")
    print(f"  reason:  {reason}")
print(f"\n{'='*70}")
EOF

# ---------------------------------------------------------------------------
# Push to GitHub or print manual scp
# ---------------------------------------------------------------------------
echo "[$(date)] Checking GITHUB_TOKEN..."
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "[$(date)] WARNING: GITHUB_TOKEN not set."
    echo "  Retrieve manually:"
    echo "  gcloud compute scp tes-cc-validation-tmp:/opt/scoring/repo/data/cc_judge_scores.jsonl data/cc_judge_scores.jsonl --zone=asia-east1-a --project=aetherart-497918"
else
    git config user.email "gaurav.gandhi2411@gmail.com"
    git config user.name "gaurav-gandhi-2411"
    git add data/cc_judge_scores.jsonl
    git commit -m "feat(data): CC session validation scores (qwen3:30b-a3b, 3 real CC sessions)"
    git push "https://${GITHUB_TOKEN}@github.com/gaurav-gandhi-2411/token-efficiency-scorer.git" master 2>&1 \
        | sed "s/${GITHUB_TOKEN}/***REDACTED***/g" \
        || echo "[$(date)] Push FAILED — retrieve manually via gcloud compute scp"
    echo "[$(date)] Push step done."
fi

echo "[$(date)] Done."
sleep 5
