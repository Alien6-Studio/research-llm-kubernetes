# Reproducing calculations and collecting new measurements

Run commands from the repository root. Replaying retained observations and
collecting new measurements are separate operations: the checks in the
[README](../README.md#reproduce-the-calculations) require neither a cluster nor a
model server. They do not repeat physical experiments or validate a production
capacity claim.

## What the offline checks cover

Python 3.12's standard library is sufficient for tests and numerical analysis.
HTTP integration tests require loopback sockets. Run the complete command
sequence in the README, including both archive-restoration commands before the
tests.

`v2.verify` checks historical compressed and decoded archive hashes,
byte-identical workloads, individual timings, lease conservation,
classifications, transport controls and committed numerical analyses.
`scripts/inventory_legacy_evidence.py --check` compares the historical archive
inventory with its retained records.

The native baseline checker restores and verifies 204 original JSON files, the
retained calibration pilot, all 45 observations, 1,490 complete native responses
in certified trials, 29 reset proofs and 112 technical warmup responses. It
recomputes the JSON's scientific fields exactly and the CSV byte for byte. The
recorded Python patch version remains visible; Python 3.12 patch differences are
the only permitted report-provenance difference.

The shared-admission checker separately restores and replays 116 original files,
all 24 observations and twelve paired differences, 25 reset proofs, 96 technical
warmup responses and 844 complete native trial response bodies. These body counts
are evidence inventories, not independent statistical repetitions. CI performs
no model generation or Kubernetes experiment.

`v2.report --check` verifies the numerical tables and CSV.
`v2.publication --check` verifies the generated `RESULTS.md` and
`ARTICLE_EVIDENCE.md`; changes to those reports belong in their generator,
`v2/publication.py`.

## Regenerate an analysis and standalone figures

```bash
python3 -m v2.analyze archives/v2-linux-primary --out analysis/v2-linux-primary/results.json
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-analysis.txt
MPLCONFIGDIR=/tmp/alien6-mpl python3 -m v2.report --figures
MPLCONFIGDIR=/tmp/alien6-mpl python3 scripts/plot_shared_admission.py
```

Rendering libraries are optional and are not installed in measured service
images. PNG/SVG figures are derived from archived request outcomes. Regenerating
them does not collect new observations.

## Recover the source that was actually executed

[`instruments/manifest.json`](../instruments/manifest.json) preserves executed
source identities. The current network allowlist is a security correction for
future collection, not a rerun of earlier experiments. Materialize captured
source into a fresh directory:

```bash
python3 scripts/materialize_instrument.py SOURCE_ID /tmp/empty-instrument-directory
```

Replace `SOURCE_ID` with the recorded identity. Raw observations never acquire a
corrected source's identity retroactively. Captured protocols and executed source
bytes remain inspectable independently of Git ancestry. See the
[evidence index](../EVIDENCE.md) and [archive guide](../archives/README.md).

## Collect a new emulator campaign

Read [the protocol](../PROTOCOL.md) and predeclare new source, trace, image and
environment identities first. Use a new output directory; never replace an
earlier run. A complete invalid block remains excluded from aggregate inference.
Its passing trials cannot be selected to repair it; individual observations may
remain visible with their original status, as in the native baseline.

Docker, kind 0.32.0 and kubectl 1.36.1 are needed. The V2 cluster is isolated as
`a6-capacity-v2`, with its own ignored kubeconfig. Do not use an application
cluster or change the normal Kubernetes context. Example emulator setup:

```bash
kind create cluster --config v2/kind.yaml --kubeconfig .kubeconfig-capacity-v2
IMAGE="alien6-capacity:v2-$(git rev-parse --short=12 HEAD)"
SOURCE_ID="$(python3 -c 'from pathlib import Path; from v2.identity import capture, verify_worktree; s = capture(Path.cwd()); verify_worktree(Path.cwd(), s.source_id); print(s.source_id)')"
docker update --cpus 4 --memory 8g --memory-swap 8g a6-capacity-v2-control-plane
docker build -f v2/Dockerfile --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" --build-arg SOURCE_ID="$SOURCE_ID" -t "$IMAGE" .
kind load docker-image "$IMAGE" --name a6-capacity-v2
python3 -m v2.run deploy --image "$IMAGE"
kubectl --kubeconfig .kubeconfig-capacity-v2 -n a6-capacity-v2 port-forward --address 127.0.0.1 service/controller 18082:8080
```

Keep the forward in another terminal. Run the complete qualification before a
comparison, using fresh output paths and the exact built image:

```bash
python3 -m v2.run block --block calibration-4 --image "$IMAGE" --out runs/my-calibration-4
python3 -m v2.run block --block primary --image "$IMAGE" --out runs/my-primary
```

The 8 CPU campaign requires a host that really supplies 8 CPU and both
qualifications. [VM provisioning](../infra/create-research-vm.sh),
[bootstrap](../infra/bootstrap-research-vm.sh) and
[CPU orchestration](../infra/run-cpu-controls.sh) document the separate VM path.
These scripts require an explicitly authorized isolated VM; they are not
commands to run on a shared hypervisor without checking their scope.

## Collect native-model observations

The native model setup, exact digest/options, observed CPU baseline and
historical qualification exclusions are documented in the
[protocol](../PROTOCOL.md) and [results](../RESULTS.md). The paired intervention
has its own [shared-admission protocol](../studies/shared-admission/PROTOCOL.md)
and [frozen manifest](../protocol/shared-admission/manifest.json).

Keep installed weights and the existing 11434 service separate from the
dedicated 11435 study server. The remote helpers require an explicitly configured
private SSH path, observer and bridge. Never expose a model API publicly or
substitute a transport fixture for actual generation. Do not launch model
comparisons after a failed qualification gate.

Every scheduled arrival, including a refusal, stays in the primary denominator.
Eventual native completion is checked separately from a correct answer received
within the client's deadline. Three trace repetitions support an exploratory
comparison of the fixed intervention; they do not establish an optimal admission
limit or a general library-performance benefit.

## Keep evidence and plans distinct

`archives/` contains lossless individually hashed observations, including failed
blocks. `analysis/` contains deterministic derived outcomes. The native baseline
index covers its 185 continuation files and all 19 files of the original prefix.
Each gzip has compressed and decoded SHA-256 values; its header time is zero and
the decoded collection timestamps are unchanged. Working native `runs/` files
can be restored from the public indexes. Monolithic transfer capsules and model
weights are not publication dependencies.

[Autoscaling](../studies/autoscaling/PROTOCOL.md) is a separate prepared study;
its metric exporter and controllers are not yet qualified. The historical
`run.py` autoscaler remains an experimental prototype. Future admission policies
require their own registered comparisons and new observation files.
