# Kubernetes client capacity for LLM applications

[![Research CI](https://github.com/Alien6-Studio/research-llm-kubernetes/actions/workflows/ci.yml/badge.svg)](https://github.com/Alien6-Studio/research-llm-kubernetes/actions/workflows/ci.yml)
[![Code license: Apache-2.0](https://img.shields.io/badge/code%20license-Apache--2.0-blue)](LICENSE)


An empirical study for the existing Alien6 article at
`/en/research/articles/ai-production-kubernetes/`.

**Research question:** how do client replicas, a shared inference capacity limit,
and admission policy affect sustainable useful throughput?

## Current status

[The protocol](PROTOCOL.md) is registered before measurements. A dedicated local
kind cluster, `a6-research`, is available with a 4-CPU, 8-GiB node limit.
The load test system is implemented and its eight tests pass. **The first 21 trials are an excluded pilot; a qualified confirmatory campaign
has not yet been executed.** [The evidence index](EVIDENCE.md) separates
implemented instrumentation from measured results.

The project uses its own kubeconfig, excluded from Git. Existing application
clusters and the user's normal Kubernetes context are outside this study.

## Planned campaigns

1. A real Kubernetes client API calling a controlled inference emulator: fixed
   quotas and service times, 1/2/4/8 replicas, regular and bursty arrivals, repeated
   trials, and explicit admission policies.
2. A dedicated local Ollama service: test which observations transfer to actual
   model generation. Keep emulator and real-model measurements separate.

Retain arrival traces, raw outcomes, full latency, refusals, queue observations,
and resource samples. Report financial cost as an estimate when only declared
unit prices are available. Measured resource use is a separate quantity.

## Repository structure

- `PROTOCOL.md`: hypotheses and controls.
- `service.py`: provider, worker, and in-cluster load-controller roles.
- `run.py`: deployment, deterministic traces, and trial orchestration.
- `test_study.py`: quota accounting, HTTP integration, and trace controls.
- `infra/`: reproducible cluster configuration.
- `runs/`: trial parameters and raw measurements.

This public repository contains research code and evidence. It contains no
customer deliverables or private application infrastructure.

## Run the study

Python 3.12's standard library is sufficient for the load system. Docker, kind,
and kubectl are required for the local cluster.

```bash
kind create cluster --config infra/kind.yaml --kubeconfig .kubeconfig-a6-research
docker update --cpus 4 --memory 8g --memory-swap 8g a6-research-control-plane
python3 -m unittest discover -p 'test_*.py'
docker build -t alien6-research-capacity:study-v2 .
kind load docker-image alien6-research-capacity:study-v2 --name a6-research
python3 run.py deploy
kubectl --kubeconfig .kubeconfig-a6-research -n a6-research port-forward service/controller 18081:8080
```

Keep the port-forward in a separate terminal, then run:

```bash
python3 run.py smoke
python3 run.py calibrate
# Run confirmatory stages only after the documented validity gates pass:
python3 run.py emulator
python3 run.py retry
python3 run.py autoscale
```

For the real-model phase, start a separate Ollama server on loopback port 11435,
using the protocol's settings. It shares installed weights while preserving
the existing service on port 11434.

```bash
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_NUM_PARALLEL=4 OLLAMA_MAX_QUEUE=256 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_CONTEXT_LENGTH=8192 ollama serve
python3 run.py ollama
```

The smoke test validates the HTTP path and is kept outside the research cohort.
Invalid trials retain their raw records and reasons. The autoscaler under test
is the explicit prototype controller in this repository, not HPA or KEDA.

## Article history

Original article publication: **2026-11-03**. Dedicated repository and new
experimental work: **12 November 2026**. See [the chronology](CHRONOLOGY.md)
for the distinction between historical publication and newly collected evidence.

[Validity gates](VALIDITY.md) document competing explanations, pilot exclusions,
and the controls required before attributing a plateau to inference capacity.
