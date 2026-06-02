#!/bin/bash
# vm_manual_setup.sh — remediation script run directly on VM when startup-script
# failed due to missing $HOME in metadata-script-runner environment.
set -euo pipefail
export HOME=/root

echo "[S1] $(date) - pulling qwen3:30b-a3b model..."
ollama pull qwen3:30b-a3b
echo "[S2] $(date) - pull done. cloning repo..."
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer.git /opt/scoring/repo
echo "[S3] $(date) - repo cloned. installing deps..."
cd /opt/scoring/repo
pip3 install httpx --quiet || /opt/conda/bin/pip install httpx --quiet
python3 -c "import numpy" 2>/dev/null || pip3 install numpy --quiet
python3 -c "
import sys
sys.path.insert(0, 'src')
import httpx, numpy
from token_efficiency.trace_digest import digest_to_text
print('deps OK: httpx numpy trace_digest importable')
"
echo "[S4] $(date) - deps verified."
echo ""
echo "=== nvidia-smi ==="
nvidia-smi
echo "=== ollama list ==="
ollama list
echo ""
echo "SETUP_COMPLETE $(date)"
