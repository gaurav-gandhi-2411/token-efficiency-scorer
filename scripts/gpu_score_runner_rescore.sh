#!/bin/bash
# gpu_score_runner_rescore.sh — B2 Step3 rescore runner (16 sessions).
#
# Input pool:    data/corpus_pool/pool_adapted.jsonl
# Session filter: data/rescore_sessions.json  (15 recoverable + 1 divergent)
# Output:        data/rescore_scores.jsonl     (separate from pool_judge_scores.jsonl)
#
# Sessions breakdown:
#   15 recoverable: <=600 turns, failed in B2 run (budget/context), num_predict fix applied
#   1  divergent:   9922d849 — scored in B2 run but diverged on laptop; needs GPU confirmation
#   3  proof-of-fix (included in 15): cc7c813e, afd82d38, 024f00be
#
# Shutdown strategy:
#   Belt 1 (watchdog): background sleep+poweroff fires if Python hangs past 1h
#   Belt 2 (EXIT trap): cleanup() fires on normal exit, error exit, or signal
#
# Usage: bash /opt/scoring/repo/scripts/gpu_score_runner_rescore.sh
# Env:   GITHUB_TOKEN — if set, pushes data/rescore_scores.jsonl to GitHub

set -euo pipefail

REPO_DIR="/opt/scoring/repo"
cd "$REPO_DIR"

# ---------------------------------------------------------------------------
# Belt 1 — Internal watchdog (1h hard kill)
# Total run is ~20 min at 45s/session × 16 sessions; 1h is generous.
# ---------------------------------------------------------------------------
(sleep 3600 && echo "[$(date)] Watchdog: 1h elapsed, forcing poweroff." && sudo poweroff) &
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
echo "[$(date)] === B2 Step3 rescore runner starting (16 sessions) ==="
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
# Preemption-safe sentinel
# ---------------------------------------------------------------------------
CLEARED_MARKER="$REPO_DIR/data/.rescore_scores_cleared"

if [ ! -f "$CLEARED_MARKER" ]; then
    echo "[$(date)] First run: clearing data/rescore_scores.jsonl..."
    python3 - <<'EOF'
path = "data/rescore_scores.jsonl"
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
# Single-session timing probe (confirms GPU speed before 16-session run)
# Warm probe uses first session in rescore_sessions.json with --force.
# ---------------------------------------------------------------------------
echo "[$(date)] Timing probe: scoring first session in rescore_sessions.json to confirm GPU speed..."

PROBE_SESSION=$(python3 -c "
import json
with open('data/rescore_sessions.json') as f:
    sessions = json.load(f)
print(sessions[0]['session_id'])
")

echo "[$(date)] Probe session_id: $PROBE_SESSION"
PROBE_START=$(date +%s)
python3 scripts/layer2_judge.py \
    --model qwen3:30b-a3b \
    --input-path data/corpus_pool/pool_adapted.jsonl \
    --output-path data/rescore_scores.jsonl \
    --mode session \
    --session-id "$PROBE_SESSION" \
    --force \
    --ollama-url http://localhost:11434
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
echo "[$(date)] Probe OK: ${PROBE_ELAPSED}s. Proceeding with full 16-session rescore run."

# ---------------------------------------------------------------------------
# Full run: 16 sessions (skip-if-scored resumes on preemption restart)
# Note: skip-if-scored checks only rescore_scores.jsonl (starts empty).
#       The probe session scored above will be skipped on the full-run pass.
# ---------------------------------------------------------------------------
echo "[$(date)] Starting B2 Step3 rescore run (16 sessions)..."
python3 scripts/layer2_judge.py \
    --model qwen3:30b-a3b \
    --input-path data/corpus_pool/pool_adapted.jsonl \
    --output-path data/rescore_scores.jsonl \
    --session-ids-file data/rescore_sessions.json \
    --ollama-url http://localhost:11434

echo "[$(date)] Scoring complete."

# ---------------------------------------------------------------------------
# Results table: highlight proof-of-fix and divergent-check sessions
# ---------------------------------------------------------------------------
python3 - <<'EOF'
import json
rows = [json.loads(l) for l in open("data/rescore_scores.jsonl") if l.strip()]
print(f"\nRescore results ({len(rows)} sessions):")
proof_pfx = {"cc7c813e", "afd82d38", "024f00be", "9922d849"}
for r in sorted(rows, key=lambda x: x["session_id"]):
    pfx = r["session_id"][:8]
    tag = "PROOF-OF-FIX" if pfx in {"cc7c813e","afd82d38","024f00be"} else ("DIVERGENT-CHECK" if pfx=="9922d849" else "")
    print(f"  {pfx}  verdict={r['verdict']:<12}  conf={r['confidence']:.2f}  {tag}")
EOF

# ---------------------------------------------------------------------------
# Verdict distribution report
# ---------------------------------------------------------------------------
python3 - <<'EOF'
import json
from collections import Counter
path = "data/rescore_scores.jsonl"
rows = [json.loads(l) for l in open(path) if l.strip()]
verdicts = [r.get("verdict","") for r in rows]
counts = Counter(verdicts)
total = len(rows)
print(f"\nVerdict distribution ({total} sessions):")
for v in ["MUCH_BETTER","BETTER","SIMILAR","WORSE","MUCH_WORSE"]:
    n = counts.get(v, 0)
    pct = n/total*100 if total else 0
    bar = "#" * (n // max(1, total//40))
    print(f"  {v:<12} {n:>4}  ({pct:5.1f}%)  {bar}")
mb_b = counts.get("MUCH_BETTER",0) + counts.get("BETTER",0)
print(f"\nCandidate gate (MUCH_BETTER+BETTER): {mb_b}/{total} = {mb_b/total*100:.1f}%")
mb_only = counts.get("MUCH_BETTER",0)
print(f"Strict gate (MUCH_BETTER only):     {mb_only}/{total} = {mb_only/total*100:.1f}%")
EOF

# ---------------------------------------------------------------------------
# Push to GitHub or print manual scp
# ---------------------------------------------------------------------------
echo "[$(date)] Checking GITHUB_TOKEN..."
if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "[$(date)] WARNING: GITHUB_TOKEN not set."
    echo "  Retrieve manually:"
    echo "  gcloud compute scp tes-pool-scoring-b2-tmp:/opt/scoring/repo/data/rescore_scores.jsonl data/rescore_scores.jsonl --zone=asia-east1-a --project=aetherart-497918"
else
    git config user.email "gaurav.gandhi2411@gmail.com"
    git config user.name "gaurav-gandhi-2411"
    git add data/rescore_scores.jsonl
    git commit -m "feat(data): B2 Step3 rescore scores (num_predict=6144, 16 sessions, L4 GPU)"
    git push "https://${GITHUB_TOKEN}@github.com/gaurav-gandhi-2411/token-efficiency-scorer.git" master 2>&1 \
        | sed "s/${GITHUB_TOKEN}/***REDACTED***/g" \
        || echo "[$(date)] Push FAILED — retrieve manually via gcloud compute scp"
    echo "[$(date)] Push step done."
fi

echo "[$(date)] Done."
sleep 5
