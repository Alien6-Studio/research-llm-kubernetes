# Kubernetes client capacity for LLM applications

[![Research CI](https://github.com/Alien6-Studio/research-llm-kubernetes/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Alien6-Studio/research-llm-kubernetes/actions/workflows/ci.yml)
[![Code license: Apache-2.0](https://img.shields.io/badge/code%20license-Apache--2.0-blue)](LICENSE)

We study what limits timely answers in an LLM application: application workers,
shared inference capacity, or work that remains queued after its caller expires.
The study separates controlled emulator interventions from actual native
inference observations.

The qualified evidence includes a 72-trial emulator matrix, quota counterfactuals
and 48 paired CPU controls. The native CPU baseline retains all 45 planned
conditions: 44 have complete native-completion certificates and one has uncertain
completion. All 28 observations in the reset-per-trial continuation pass their
individual checks. Its lifecycle differs from the original 17-observation epoch,
so the combined table is descriptive and is not a qualified causal comparison.
No HPA/KEDA or proposed-library benefit has been measured.

A separate [paired native-admission experiment](studies/shared-admission/PROTOCOL.md)
completed all 24 trials and their native-completion checks. A shared limit of
one generation produced fewer timely correct responses in eleven of twelve
pairs and more in one. The three repetitions per worker/load condition describe
this fixed policy and workload; they establish no general library benefit.
These observations remain separate from the baseline and emulator comparisons.

## Read the study

| Deliverable | Purpose |
| --- | --- |
| [VALIDITY.md](VALIDITY.md) | Interpretation boundary, exclusions and competing explanations |
| [PROTOCOL.md](PROTOCOL.md) | Hypotheses, frozen matrices, exclusions and prospective continuations |
| [Benchmark V2](v2/) | Async measurement, atomic leases, Kubernetes orchestration and offline replay |
| [RESULTS.md](RESULTS.md) | Actual outcomes, uncertainty, negative results, resources and limits |
| [ARTICLE_EVIDENCE.md](ARTICLE_EVIDENCE.md) | Supported statements and open questions for future writing |
| [Shared native admission](studies/shared-admission/PROTOCOL.md) | Fixed K=1 intervention, 24-trial paired design and interpretation limits |

![Measured replica and admission outcomes](analysis/v2-study/replicas-and-admission.png)

[Evidence index](EVIDENCE.md) · [All condition tables](analysis/v2-study/tables.md) ·
[Trial CSV](analysis/v2-study/trials.csv) · [Full derived analysis](analysis/v2-study/results.json)

The actual-model evidence has its own [45-row report](analysis/v2-ollama-fra6s004-cpu-continuation/results.json),
[trial CSV](analysis/v2-ollama-fra6s004-cpu-continuation/trials.csv) and
[lossless file index](archives/cpu-native-baseline/evidence-index.json).
The admission comparison has a separate [24-trial report](analysis/v2-ollama-shared-admission/results.json),
[trial CSV](analysis/v2-ollama-shared-admission/trials.csv) and
[lossless file index](archives/shared-admission/evidence-index.json).
Native completion and a correct response within the ten-second client deadline
are different outcomes. A late model answer never improves the client score.

## Reproduce the calculations without a cluster or model

Python 3.12's standard library is sufficient for tests and numerical analysis.
Loopback sockets must be allowed for HTTP integration tests.

```bash
python3 scripts/check_cpu_continuation.py --restore-only
python3 scripts/check_shared_admission.py --restore-only
python3 -m unittest discover -p 'test_*.py'
python3 scripts/analyze_calibration.py --check
python3 scripts/analyze_focused.py --check
python3 scripts/verify_evidence.py
python3 -m v2.verify
python3 scripts/check_cpu_continuation.py
python3 scripts/check_shared_admission.py --mode final
python3 -m v2.report --check
python3 -m v2.publication --check
```

`v2.verify` checks historical compressed/decoded archive hashes, byte-identical workloads,
individual timings, lease conservation, classifications, transport controls and
committed numerical analyses. The native checker separately restores and verifies
204 original JSON files, the retained calibration pilot, all 45 observations,
1,490 complete native responses in certified trials, 29 reset proofs and 112
technical warmup responses. It recomputes the JSON's scientific fields exactly
and the CSV byte for byte. The recorded Python patch version remains visible;
Python 3.12 patch differences are the only permitted report-provenance difference.
The shared-admission checker separately replays 116 original files, all 24
observations and twelve paired differences, 25 reset proofs, 96 technical warmup
responses and 844 complete native trial response bodies. These body counts are
evidence inventories, not independent statistical repetitions. CI performs no
model generation or Kubernetes experiment.

To regenerate one analysis and the standalone figures:

```bash
python3 -m v2.analyze archives/v2-linux-primary --out analysis/v2-linux-primary/results.json
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-analysis.txt
MPLCONFIGDIR=/tmp/alien6-mpl python3 -m v2.report --figures
```

Rendering libraries are optional and are not installed in measured service
images. PNG/SVG figures come from archived request outcomes; they contain no
invented benchmark points.

## What the native-admission comparison tests

An application can stop waiting after ten seconds while Ollama continues its
generation. Adding application workers can then admit more work without making
more answers arrive in time. We compare two treatments on identical arrival
traces: forward every call through the existing worker path, or refuse a new
call immediately while a shared native-generation permit is occupied. Both
treatments use the same proxy, model, resources and process-reset procedure.

The shared limit is one generation. This value follows a fixed rule applied to
the retained calibration, which did not establish that even one concurrent
generation reliably meets the ten-second deadline. The treatment may therefore
reduce late work, useful answers, or both. Every scheduled arrival, including a
refusal, stays in the primary denominator. Eventual native completion is checked
separately from a correct answer received within the client's deadline.

The design has twelve pairs across one/eight workers, two offered loads and
three trace repetitions. A global-minus-bypass difference estimates this
specific capacity-cap intervention; it does not isolate coordination overhead
from the changed concurrency budget. Three repetitions support an exploratory
comparison, not a general library-performance claim. The
[frozen manifest](protocol/shared-admission/manifest.json) fixes the arm order,
traces, capacity rule and evidence locations before collection.

This experiment informs a possible reusable admission component: one shared
budget, permits tied to actual native completion, and explicit handling of
unknown completion. Whether that component improves useful client outcomes is
an open empirical question until the paired observations are checked. The
research proxy is an instrument, not a production library release.

## Collect a new campaign

Read the protocol and predeclare new source, trace, image and environment
identities first. Use a new output directory: never replace an earlier run.
A complete invalid block remains excluded from aggregate inference; do not select
its passing trials to repair it. Its individual observations may remain visible
with their original status, as in the native baseline.

Docker, kind 0.32.0 and kubectl 1.36.1 are needed. The V2 cluster is isolated as
`a6-capacity-v2`, with its own ignored kubeconfig; do not use an application
cluster or change the normal Kubernetes context. Example emulator setup:

```bash
kind create cluster --config v2/kind.yaml --kubeconfig .kubeconfig-capacity-v2
IMAGE="alien6-capacity:v2-$(git rev-parse --short=12 HEAD)"
docker update --cpus 4 --memory 8g --memory-swap 8g a6-capacity-v2-control-plane
docker build -f v2/Dockerfile --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" -t "$IMAGE" .
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

The 8 CPU campaign requires a host that really supplies 8 CPU and both qualifications.
[VM provisioning](infra/create-research-vm.sh), [bootstrap](infra/bootstrap-research-vm.sh)
and [CPU orchestration](infra/run-cpu-controls.sh) document the separate VM path.
They are operational scripts for an explicitly authorized isolated VM, not
commands to run on a shared hypervisor without checking their scope.

The native model setup, exact digest/options, observed CPU baseline and historical
qualification exclusions are documented in the protocol and results. Keep installed weights and the
existing 11434 service separate from the dedicated 11435 study server. The remote
helpers require an explicitly configured private SSH path, observer and bridge;
they must never expose a model API publicly or substitute a transport fixture
for actual generation. Do not launch model comparisons after a failed gate.

[Autoscaling](studies/autoscaling/PROTOCOL.md) is a separate prepared study; its
metric exporter/controllers are not yet qualified. The historical `run.py`
autoscaler remains an experimental prototype.

## Retained evidence and scope

`archives/` contains lossless individually hashed observations, including failed
blocks. `analysis/` contains deterministic derived outcomes. The native baseline
index covers its 185 continuation files and all 19 files of the original prefix.
Each gzip has compressed and decoded SHA-256 values; its header time is zero and
the decoded collection timestamps are unchanged. The largest file is about
7.6 MB. Working `runs/` are ignored and can be restored from these public copies;
monolithic transfer capsules and model weights are not publication dependencies.
Captured protocols, executed source bytes and exclusions remain inspectable
independently of Git ancestry.
See [PROTOCOL.md](PROTOCOL.md), [VALIDITY.md](VALIDITY.md),
[CALIBRATION_RESULTS.md](CALIBRATION_RESULTS.md), [FOCUSED_RESULTS.md](FOCUSED_RESULTS.md)
and the captured methods in `archives/legacy-methods/`.

Code, documentation and figures are in English. This public repository contains
research evidence, no customer deliverables. The Alien 6 site and article are not
modified by this study.

`instruments/manifest.json` preserves executed source identities. The current
network allowlist is a security correction for future collection, not a re-run
of the experiments. Materialize captured source into a fresh directory with
`python3 scripts/materialize_instrument.py SOURCE_ID /tmp/empty-instrument-directory`.
Raw observations never acquire the corrected source's identity retroactively.
