#!/bin/bash
cd /opt/scoring/repo
echo "[timing] Starting single-session judge: 2861967e06780fdc (9 turns, swe_agent, resolved)"
START=$(date +%s%N)
python3 scripts/layer2_judge.py \
  --model qwen3:30b-a3b \
  --session-id 2861967e06780fdc \
  --mode session \
  --ollama-url http://localhost:11434 2>&1
END=$(date +%s%N)
ELAPSED_MS=$(( (END - START) / 1000000 ))
echo "[timing] Wall-clock: ${ELAPSED_MS}ms  (~$(( ELAPSED_MS / 1000 ))s)"
echo ""
echo "=== nvidia-smi after model loaded ==="
nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv
