#!/usr/bin/env bash
set -euo pipefail
# Run from an isolated checkout; do not reuse an archived cohort directory.
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--help" ]]; then
  cat <<'HELP'
Usage: STUDY_MODEL_IMAGE=IMAGE STUDY_MODEL_PLAN=PLAN STUDY_COHORT_PREFIX=UNIQUE_PREFIX \
       bash infra/run-remote-model-controls.sh

Requires the dedicated cluster, private inference/monitor tunnels, and a registered
source snapshot matching IMAGE. Performs transport qualification, all model-path
controls, warmup, then the full real-model block. Stops on any failed gate.
HELP
  exit 0
fi
: "${STUDY_MODEL_IMAGE:?Set the image built from the registered instrument snapshot}"
: "${STUDY_MODEL_PLAN:?Set the frozen plan path}"
: "${STUDY_COHORT_PREFIX:?Set a fresh cohort prefix (v2-...)}"
[[ "$STUDY_COHORT_PREFIX" =~ ^v2-[a-zA-Z0-9-]+$ ]] || { echo "Invalid cohort prefix" >&2; exit 2; }
for suffix in network model-qualification model-warmup real-model; do
  [[ ! -e "runs/$STUDY_COHORT_PREFIX-$suffix" ]] || { echo "Refusing to overwrite a cohort" >&2; exit 2; }
done
export STUDY_INFERENCE_ORIGIN="${STUDY_INFERENCE_ORIGIN:-http://172.18.0.1:11435}"
export STUDY_OLLAMA_URL="${STUDY_OLLAMA_URL:-http://127.0.0.1:11435}"
export STUDY_INFERENCE_MONITOR="${STUDY_INFERENCE_MONITOR:-http://127.0.0.1:11436}"
IMAGE="$STUDY_MODEL_IMAGE"
PLAN="$STUDY_MODEL_PLAN"
PREFIX="$STUDY_COHORT_PREFIX"
python3 - <<'PY'
import json, os
from pathlib import Path
if not __debug__:
    raise RuntimeError('Scientific collection requires Python assertions; disable optimization')
from v2.identity import capture, verify_worktree
from v2.remote_plan import derive
root = Path.cwd()
snapshot = capture(root)
verify_worktree(root, snapshot.source_id)
plan = json.loads(Path(os.environ['STUDY_MODEL_PLAN']).read_text())
from v2.cpu_campaigns import CPU_CAMPAIGNS, cpu_campaign_for_plan
if plan.get('continuation', {}).get('cohort') in {data['cohort'] for data in CPU_CAMPAIGNS.values()}:
    from v2.verify import replay_cpu_plan
    if plan['continuation'].get('source_id') != snapshot.source_id or plan['continuation'].get('git_base') != snapshot.git_base:
        raise RuntimeError('CPU plan does not match the registered collection source and Git base')
    campaign = cpu_campaign_for_plan(plan)
    if campaign['terminal']:
        raise RuntimeError('The previous CPU attempt is terminal; use a registered fresh campaign')
    if os.environ['STUDY_COHORT_PREFIX'] != campaign['prefix']:
        raise RuntimeError('CPU output prefix differs from the registered campaign')
    expected = replay_cpu_plan(plan)
else:
    expected = derive('172.18.0.1')
for field, value in expected.items():
    if plan.get(field) != value:
        raise RuntimeError(f'Plan differs from the registered complete study: {field}')
if len(plan['blocks']['model-qualification']) != 12 or len(plan['blocks']['real-model']) != 45:
    raise RuntimeError('The registered study requires all 12 controls and 45 model trials')
PY
mkdir -p study-logs
python3 -m v2.run deploy --image "$IMAGE"
kubectl --kubeconfig .kubeconfig-capacity-v2 -n a6-capacity-v2 \
  port-forward --address 127.0.0.1 service/controller 18082:8080 >study-logs/remote-model-control-forward.txt 2>&1 &
FORWARD_PID=$!
trap 'kill "$FORWARD_PID" 2>/dev/null || true' EXIT
python3 - <<'PY'
import hashlib,json,time,urllib.request
from pathlib import Path
from v2.service import code_hash
from v2.identity import capture, verify_worktree
snapshot = capture(Path.cwd())
verify_worktree(Path.cwd(), snapshot.source_id)
expected = hashlib.sha256(Path('v2/model_service.py').read_bytes()).hexdigest()
for attempt in range(60):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18082/stats', timeout=2) as response:
            state = json.load(response)
            assert state['code_sha256'] == code_hash()
            assert state['model_controller_sha256'] == expected
            assert state['source_id'] == snapshot.source_id
        break
    except Exception:
        if attempt == 59: raise
        time.sleep(.5)
PY
python3 -m v2.network_probe --url "${STUDY_TRANSPORT_CONTROL_URL:-$STUDY_INFERENCE_ORIGIN/transport-control}" \
  --plan "$PLAN" --out "runs/$PREFIX-network/control.json" >"study-logs/$PREFIX-network.txt" 2>&1
python3 -m v2.model block --plan "$PLAN" --block model-qualification --image "$IMAGE" \
  --out "runs/$PREFIX-model-qualification" >"study-logs/$PREFIX-model-qualification.txt" 2>&1
python3 -m v2.model warmup --out "runs/$PREFIX-model-warmup/warmup.json" \
  >"study-logs/$PREFIX-model-warmup.txt" 2>&1
python3 -m v2.model block --plan "$PLAN" --block real-model --image "$IMAGE" \
  --out "runs/$PREFIX-real-model" >"study-logs/$PREFIX-real-model.txt" 2>&1
