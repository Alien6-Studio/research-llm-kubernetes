#!/usr/bin/env bash
set -euo pipefail
cd /opt/alien6-capacity-v2
mkdir -p study-logs
kubectl --kubeconfig .kubeconfig-capacity-v2 -n a6-capacity-v2 \
  port-forward --address 127.0.0.1 service/controller 18082:8080 >study-logs/control-forward.txt 2>&1 &
FORWARD_PID=$!
trap 'kill "$FORWARD_PID" 2>/dev/null || true' EXIT
python3 - <<'PY'
import json,time,urllib.request
from v2.service import code_hash
for attempt in range(50):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18082/stats', timeout=2) as response:
            assert json.load(response)['code_sha256'] == code_hash()
        break
    except Exception:
        if attempt == 49: raise
        time.sleep(.2)
PY
for BLOCK in calibration-4 calibration-8 cpu-sensitivity; do
  python3 -m v2.run block --block "$BLOCK" --out "runs/v2-independent-$BLOCK" \
    >"study-logs/$BLOCK.txt" 2>&1
done
