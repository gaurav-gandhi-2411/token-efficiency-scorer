#!/bin/bash
# vm_startup_b2.sh — B2 pool scoring startup (3.5h hard watchdog, L4 GPU, asia-east1)
# Nearly identical to vm_startup.sh; the only difference is the watchdog duration:
#   B1: 14400s (4h) | B2: 12600s (3.5h) — B2 run is shorter (181 sessions, ~2.5h expected)
exec > >(tee /var/log/startup.log) 2>&1
set -euo pipefail

# GCP metadata-script-runner does not set $HOME; Ollama panics without it.
export HOME=/root

echo "[$(date)] === VM startup script starting ==="

# Global hard watchdog: VM shuts down 3.5h after boot regardless of script state.
# Independent of gpu_score_runner_b2.sh's internal 3h watchdog (belt-and-suspenders).
systemd-run --on-active=12600 --unit=vm-hard-stop /sbin/poweroff
echo "[$(date)] Global hard watchdog set: VM shuts in 3.5h from now"

# Install Ollama with CUDA support
echo "[$(date)] Installing Ollama..."
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable ollama
systemctl start ollama
sleep 10
echo "[$(date)] Ollama installed and started"

# Pull the judge model (~20GB, Q4 quant — may take 10-20 min on GCP bandwidth)
echo "[$(date)] Pulling qwen3:30b-a3b model..."
ollama pull qwen3:30b-a3b
echo "[$(date)] Model pull complete"

# Install git (usually present on DLVM, ensure it)
apt-get update -qq 2>/dev/null || true
apt-get install -y -q git 2>/dev/null || true

# Clone the scoring repo
mkdir -p /opt/scoring
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer.git /opt/scoring/repo
# Make the repo writable by the default GCP SSH user (not just root).
GCLOUD_USER=$(getent passwd 1000 | cut -d: -f1 2>/dev/null || echo "")
if [ -n "$GCLOUD_USER" ]; then
    chown -R "$GCLOUD_USER":"$GCLOUD_USER" /opt/scoring/repo
fi
echo "[$(date)] Repo cloned"

# Install the only external Python deps needed by the scoring chain:
#   layer2_judge.py → httpx
#   layer2_judge.py → trace_digest → layer1_features → numpy
# numpy is pre-installed on DLVM (PyTorch dep), but we verify and install if absent.
# No uv lockfile exists in this repo — do NOT use uv sync.
cd /opt/scoring/repo
# Ensure pip is available — cu129 images ship without it.
python3 -m ensurepip --upgrade 2>/dev/null \
  || apt-get install -y -q python3-pip 2>/dev/null \
  || true
pip3 install httpx --quiet \
  || /opt/conda/bin/pip install httpx --quiet \
  || python3 -m pip install httpx --quiet
# numpy: install only if missing (DLVM already has it)
python3 -c "import numpy" 2>/dev/null \
  || pip3 install numpy --quiet \
  || /opt/conda/bin/pip install numpy --quiet
# Smoke-test the full import chain before declaring deps OK
python3 -c "
import sys
sys.path.insert(0, 'src')
import httpx, numpy
from token_efficiency.trace_digest import digest_to_text
print('deps OK: httpx numpy trace_digest all importable')
"
echo "[$(date)] Python deps installed"

# GPU verification
echo "[$(date)] === nvidia-smi ==="
nvidia-smi
echo "[$(date)] === End nvidia-smi ==="

# Write ready marker (orchestrator polls this via serial port output)
echo "SETUP_COMPLETE $(date)" > /var/log/setup_complete
echo "[$(date)] === SETUP COMPLETE - VM ready for scoring ==="

# Final confirmation: print loaded models (appears in serial output)
ollama list
