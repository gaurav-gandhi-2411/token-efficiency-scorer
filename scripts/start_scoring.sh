#!/bin/bash
# start_scoring.sh — launch gpu_score_runner.sh as a detached nohup on the VM.
# Run via: bash /tmp/start_scoring.sh
nohup bash /opt/scoring/repo/scripts/gpu_score_runner.sh \
  > /tmp/scoring.log 2>&1 &
echo "SCORING_PID=$!"
echo "Log: tail -f /tmp/scoring.log"
