#!/usr/bin/env bash
set -euo pipefail
cd /opt/alien6-capacity-v2
mkdir -p study-logs
export STUDY_INFERENCE_ORIGIN=http://172.18.0.1:11435
export STUDY_OLLAMA_URL=http://127.0.0.1:11435
export STUDY_INFERENCE_MONITOR=http://127.0.0.1:11436
IMAGE="alien6-capacity:model-v2-$(git rev-parse --short=12 HEAD)"
PLAN=protocol/v2-remote-model-plan.json
PREFIX="${STUDY_COHORT_PREFIX:-v2-remote}"
python3 -m v2.run deploy --image "$IMAGE"
kubectl --kubeconfig .kubeconfig-capacity-v2 -n a6-capacity-v2 \
  port-forward --address 127.0.0.1 service/controller 18082:8080 >study-logs/remote-model-control-forward.txt 2>&1 &
FORWARD_PID=$!
trap 'kill "$FORWARD_PID" 2>/dev/null || true' EXIT
python3 - <<'PY'
import hashlib,json,time,urllib.request
from pathlib import Path
from v2.service import code_hash
expected = hashlib.sha256(Path('v2/model_service.py').read_bytes()).hexdigest()
for attempt in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18082/stats', timeout=2) as response:
            state = json.load(response)
            assert state['code_sha256'] == code_hash()
            assert state['model_controller_sha256'] == expected
        break
    except Exception:
        if attempt == 59: raise
        time.sleep(.5)
PY
python3 -m v2.network_probe --url "${STUDY_TRANSPORT_CONTROL_URL:-http://172.18.0.1:11435/api/show}" \
  --out "runs/$PREFIX-network/control.json" >"study-logs/$PREFIX-network.txt" 2>&1
python3 -m v2.model block --plan "$PLAN" --block model-qualification --image "$IMAGE" \
  --out "runs/$PREFIX-model-qualification" >"study-logs/$PREFIX-model-qualification.txt" 2>&1
python3 -m v2.model warmup --out "runs/$PREFIX-model-warmup/warmup.json" \
  >"study-logs/$PREFIX-model-warmup.txt" 2>&1
python3 -m v2.model block --plan "$PLAN" --block real-model --image "$IMAGE" \
  --out "runs/$PREFIX-real-model" >"study-logs/$PREFIX-real-model.txt" 2>&1
