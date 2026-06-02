#!/bin/bash
# provision_gpu_vm_b2.sh — Run LOCALLY to provision the B2 pool scoring VM.
# Creates tes-pool-scoring-b2-tmp spot VM in aetherart-497918.
#
# Primary zone: asia-east1-a (confirmed available in B1 reports 06/07)
# Fallback zone: asia-east1-b
#
# Usage: bash scripts/provision_gpu_vm_b2.sh [ZONE]
#   ZONE defaults to asia-east1-a; pass asia-east1-b as $1 if -a is out of spot capacity.
#
# Preemption resilience design:
#   --instance-termination-action=STOP means preemption STOPS the VM (preserving the boot disk).
#   JSONL is written to /opt/scoring/repo/data/ on the persistent boot disk.
#   On restart, layer2_judge.py skip-if-scored logic resumes automatically.
#   No ephemeral local SSD is used — all data survives preemption.

set -euo pipefail

PROJECT="aetherart-497918"
VM_NAME="tes-pool-scoring-b2-tmp"
ZONE="${1:-asia-east1-a}"
FALLBACK_ZONE="asia-east1-b"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STARTUP_SCRIPT="$SCRIPT_DIR/vm_startup_b2.sh"

echo "[$(date)] === Checking prerequisites ==="

# Verify quota is no longer 0/0
GLOBAL_LIMIT=$(gcloud compute project-info describe --project="$PROJECT" \
  --format="json(quotas)" | python3 -c "
import json, sys
data = json.load(sys.stdin)
q = next((x for x in data.get('quotas', []) if x.get('metric') == 'GPUS_ALL_REGIONS'), None)
print(int(q['limit']) if q else 0)
")
if [[ "$GLOBAL_LIMIT" -eq 0 ]]; then
  echo "ERROR: GPUS_ALL_REGIONS limit is still 0. Wait for quota approval."
  echo "Monitor: https://console.cloud.google.com/iam-admin/quotas?project=aetherart-497918"
  exit 1
fi
echo "[$(date)] GPUS_ALL_REGIONS limit = $GLOBAL_LIMIT — quota approved."

# Find the latest CUDA 12 DLVM image
echo "[$(date)] Finding latest DLVM CUDA 12 image..."
IMAGE_FAMILY=$(gcloud compute images list \
  --project=deeplearning-platform-release \
  --filter="family:common-cu12" \
  --sort-by="~creationTimestamp" \
  --limit=1 \
  --format="value(family)")
echo "[$(date)] Using image family: $IMAGE_FAMILY"

# Create the VM (try primary zone, fall back to secondary)
echo "[$(date)] Creating VM $VM_NAME in $ZONE ..."
if gcloud compute instances create "$VM_NAME" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --machine-type=g2-standard-8 \
  --provisioning-model=SPOT \
  --instance-termination-action=STOP \
  --image-family="$IMAGE_FAMILY" \
  --image-project=deeplearning-platform-release \
  --boot-disk-size=100GB \
  --boot-disk-type=pd-balanced \
  --scopes=cloud-platform \
  --metadata-from-file=startup-script="$STARTUP_SCRIPT" 2>&1; then
  USED_ZONE="$ZONE"
else
  echo "[$(date)] $ZONE unavailable (spot capacity). Trying $FALLBACK_ZONE..."
  gcloud compute instances create "$VM_NAME" \
    --project="$PROJECT" \
    --zone="$FALLBACK_ZONE" \
    --machine-type=g2-standard-8 \
    --provisioning-model=SPOT \
    --instance-termination-action=STOP \
    --image-family="$IMAGE_FAMILY" \
    --image-project=deeplearning-platform-release \
    --boot-disk-size=100GB \
    --boot-disk-type=pd-balanced \
    --scopes=cloud-platform \
    --metadata-from-file=startup-script="$STARTUP_SCRIPT"
  USED_ZONE="$FALLBACK_ZONE"
fi

echo ""
EXT_IP=$(gcloud compute instances describe "$VM_NAME" \
  --project="$PROJECT" --zone="$USED_ZONE" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)")
echo "[$(date)] VM READY: $VM_NAME  zone=$USED_ZONE  ip=$EXT_IP"

# Ensure $10 budget alert exists for the project
echo "[$(date)] Checking/creating budget alert at \$10 USD..."
gcloud billing budgets create \
  --billing-account="$(gcloud billing projects describe $PROJECT --format='value(billingAccountName)' | sed 's|billingAccounts/||')" \
  --display-name="gpu-judge-safety-net-b2" \
  --budget-amount=10USD \
  --threshold-rule=percent=80 \
  --threshold-rule=percent=100 \
  2>&1 || echo "[$(date)] Budget alert creation skipped (may already exist or insufficient billing permissions)"

echo ""
echo "Next steps:"
echo "  1. Wait ~20 min for startup (model pull). Monitor:"
echo "     gcloud compute instances get-serial-port-output $VM_NAME --project=$PROJECT --zone=$USED_ZONE 2>&1 | tail -30"
echo "  2. When serial output shows 'SETUP COMPLETE', confirm GPU with:"
echo "     gcloud compute ssh $VM_NAME --project=$PROJECT --zone=$USED_ZONE -- nvidia-smi && ollama list"
echo "  3. Set GITHUB_TOKEN then run the B2 pool scoring runner:"
echo "     gcloud compute ssh $VM_NAME --project=$PROJECT --zone=$USED_ZONE -- 'GITHUB_TOKEN=\$GITHUB_TOKEN bash /opt/scoring/repo/scripts/gpu_score_runner_b2.sh 2>&1 | tee /var/log/scoring.log'"
echo "  4. Output to retrieve: data/pool_judge_scores.jsonl"
echo "     gcloud compute scp $VM_NAME:/opt/scoring/repo/data/pool_judge_scores.jsonl data/pool_judge_scores.jsonl --zone=$USED_ZONE --project=$PROJECT"
echo "  5. VM shuts itself down when done. Verify teardown:"
echo "     gcloud compute instances describe $VM_NAME --project=$PROJECT --zone=$USED_ZONE --format=value(status)"
