#!/bin/bash
# Startup script for qwen3-judge-gpu VM
exec > >(tee /var/log/startup.log) 2>&1
set -euo pipefail

echo "[$(date)] === VM startup script starting ==="

# Global hard watchdog: VM shuts down 4h after boot regardless of script state
# This is independent of the scoring runner script's internal watchdog
systemd-run --on-active=14400 --unit=vm-hard-stop /sbin/poweroff
echo "[$(date)] Global hard watchdog set: VM shuts in 4h from now"

# Install Ollama with CUDA support
echo "[$(date)] Installing Ollama..."
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable ollama
systemctl start ollama
sleep 10
echo "[$(date)] Ollama installed and started"

# Pull the judge model (~20GB, Q4 quant - may take 10-20 min on GCP bandwidth)
echo "[$(date)] Pulling qwen3:30b-a3b model..."
ollama pull qwen3:30b-a3b
echo "[$(date)] Model pull complete"

# Install git (usually present on DLVM, ensure it)
apt-get update -qq 2>/dev/null || true
apt-get install -y -q git 2>/dev/null || true

# Clone the scoring repo
mkdir -p /opt/scoring
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer.git /opt/scoring/repo
echo "[$(date)] Repo cloned"

# Install Python deps via uv
cd /opt/scoring/repo
pip3 install uv --quiet
uv sync --frozen 2>&1 || pip3 install httpx pydantic pydantic-settings structlog pyyaml
echo "[$(date)] Python deps installed"

# GPU verification
echo "[$(date)] === nvidia-smi ==="
nvidia-smi
echo "[$(date)] === End nvidia-smi ==="
ollama list

# Write ready marker (orchestrator polls this)
echo "SETUP_COMPLETE $(date)" > /var/log/setup_complete
echo "[$(date)] === SETUP COMPLETE - VM ready for scoring ==="
