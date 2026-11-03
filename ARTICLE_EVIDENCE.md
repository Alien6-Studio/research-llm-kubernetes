# Article evidence ledger

Source material for later writing; no article is written here. The scientific question is when scaling clients yields timely useful tasks, and whether shared capacity accounting is enough to deliver them. The architectural boundary is between the client deadline, worker queue and provider-owned ledger.

## Apparatus and hypotheses

Read [PROTOCOL.md](PROTOCOL.md) for H1 replicas/headroom, H2 quota counterfactuals, H3 admission, H4 real-model transfer. The collected primary matrix has 72 trials; quota 12, provider-only 12, CPU 48 are separate controls, with their complete qualification blocks. Three trace repetitions are the experimental units. Workload: 180/s regular Poisson or alternating 180/18/s bursts, 3 s warmup, 10 s cohort, 2 s deadline, 4 active slots +32 queue places/worker; randomized trial order and exact frozen traces. Real Qwen comparisons use actual token records and have a separate failed qualification status.

## Citable numerical findings

Every number in [RESULTS.md](RESULTS.md) is generated from the files below. A row in `analysis/v2-study/trials.csv` is one trial, never a request-level replication. The individual archive for a trial is located by `trial_id` in that dataset’s archive manifest; `source_relative_path` is the original collection path, not a download URL.

| Claim / source | Metric and denominator | Comparison and limits |
| --- | --- | --- |
| Replica curve: 18.87 / 39.63 / 76.27 / 170.73/s. `analysis/v2-linux-primary/results.json`, trials filtered profile=unlimited,policy=none | `useful_cohort_per_second`; useful/10s per trial; n = 3 paired repetitions, same arrival denominator retained | 1/2/4/8 workers; finite offered load, emulator only. All per-trial values and uncertainty are in `RESULTS.md` / `contrasts[]` |
| Replica curve: 19.60 / 25.20 / 25.10 / 25.47/s. `analysis/v2-linux-primary/results.json`, trials filtered profile=quota,policy=none | `useful_cohort_per_second`; useful/10s per trial; n = 3 paired repetitions, same arrival denominator retained | 1/2/4/8 workers; finite offered load, emulator only. All per-trial values and uncertainty are in `RESULTS.md` / `contrasts[]` |
| Correct accounting yet lost utility: 487/762 consumed tasks have no timely useful client outcome (63.91%). `analysis/v2-linux-primary/results.json` | Sum `consumed_without_timely_client_outcome` / sum `provider_consumed` across 3 quota/global/8 trials; this is a consumed-work denominator, not all arrivals | Compare quota/none/8 using the same fields and paired trace seeds; 4 held slots, 32-place queue, no deadline-feasibility scheduling |
| Quota movement: `analysis/v2-linux-provider-control/results.json` and `analysis/v2-linux-quota-sensitivity/results.json` | `useful_cohort_per_second`, useful/10s per trial; n = 3 per condition | Direct provider 20k/40k/80k versus unlimited; global-worker 20k/40k/80k. Emulator units, not LLM tokens; do not pool paths |
| CPU explanation: `analysis/v2-independent-cpu-sensitivity/results.json`; combined `contrasts[]` with question=`CPU sensitivity` | Paired 4→8 CPU differences at fixed profile/policy/replicas; n = 3 per contrast | Real 8-vCPU VM; fresh randomized CPU arms. Do not equate a nonsignificant difference with equivalence or pool with GitHub hardware |
| Deadline mechanism: `archives/v2-linux-primary/primary-a8cd65769fffe9cb.json.gz` / `unlimited:0:1776` | Queue 1.810s, admission 0.140s, provider HTTP 0.147s; one selected request out of 178 non-tail consumed-unsuccessful candidates | Post hoc median worker-duration illustration in one trial, not a prevalence estimate; client outcome on generator clock, stages on worker clock |

Every other citable comparison is in `analysis/v2-study/results.json`, `contrasts[]`: exact left/right filters, metric, three paired differences, mean, df 2 interval and prospective planning sensitivity. `RESULTS.md` prints all of them. Claim only the condition in that row. Request outcomes, stage times, status counts and resources are in dataset `trials[]`; raw records are under the matching `archives/v2-*/` directory. `v2.verify` replays those from the archives, not from handwritten summaries.

## Failed actual-model instruments

| Data | Metric / denominator | Allowed interpretation |
| --- | --- | --- |
| `analysis/v2-local-real-model/results.json`, `archives/v2-local-real-model/` |30/45 trials pass individual integrity gates; whole 45 excluded | Cannot select passing trials to claim real-model scaling. All generation bodies/token counts remain inspectable |
| `archives/v2-model-pilot/`, `protocol/v2-model/plan.json` |4 warmup +48 measured classifications; closed-loop 16 per concurrency 1/2/4; best observed 4.689/s | Load selection only; no qualified Kubernetes replica comparison |
| `archives/v2-remote-network/`, `archives/v2-remote-compressed-network/`, `archives/v2-remote-fixture-network/` |Three headroom traces per block; p95 latency ≤100 ms required in every repetition; all 3 blocks fail as a whole | Transport instrument unqualified despite timely emission/HTTP success. Metadata/fixture controls are not inference throughput; unique cause remains unidentified |
| `studies/autoscaling/PROTOCOL.md` and manifests |No completed HPA/KEDA measurement denominator | Prepared only |

## Code actually used

The exact measured source is preserved as inert bytes in `instruments/manifest.json`. Select the recorded source ID from a raw manifest. The following ledger conversion comes from the primary measured instrument; it is not hypothetical library code.

```python
def consume(self, identifier, units):
    with self.lock:
        now = self.clock()
        self._expire(now)
        reservation = self.reservations.get(identifier)
        if not reservation or reservation['state'] != 'reserved' or reservation['units'] != units:
            self._event('consume_refused', identifier, now, units=units,
                        state=reservation['state'] if reservation else 'unknown')
            return False
        reservation['state'] = 'consumed'
        window = self.windows[reservation['window']]
        window['consumed_units'] += units
        window['consumed_requests'] += 1
        self._event('consumed', identifier, now, window=reservation['window'], units=units)
        return True
```

`v2/service.py::invoke` in that same captured source records planned-arrival deadlines; `v2/analyze.py::summarize` recomputes timely useful results from archived client/provider/worker observations. Current code adds server-owned destination validation; no throughput observation is retroactively assigned to that security change.

## Figures, exact sources and commands

| Standalone SVG/PNG under `analysis/v2-study/` | Source in `analysis/v2-study/results.json` |
| --- | --- |
| `replicas-and-admission` | `datasets.v2-linux-primary.trials`, profile/policy/replicas/useful_cohort_per_second; show all 3 repetitions |
| `quota-counterfactual` | provider-only/quota-sensitivity datasets, condition quotas; n = 3 per arm |
| `cpu-counterfactual` | paired CPU sensitivity contrasts, df 2 intervals; no equivalence claim |
| `deadline-and-quota-consumption` | primary quota/8 none/global; provider_consumed,useful,late consumption plus rule-selected illustration |

```sh
python -m unittest discover -p "test_*.py"
python scripts/verify_evidence.py
python -m v2.verify
python -m v2.report --check
python -m v2.publication --check
python -m pip install -r requirements-analysis.txt
python -m v2.report --figures --output /tmp/study-figures
```

No Kubernetes or model server is needed for these calculations. Collection commands and environment gates are in `README.md` / `PROTOCOL.md`; new collection must use a fresh directory and complete qualification.

## Editorial permissions and engineering hypothesis

**Established within this emulator apparatus:** headroom permits useful replica scaling; changing a shared quota moves the limitation; an atomic provider ledger alone does not guarantee useful scheduling. **Exploratory:** numerical effect sizes and 3-repetition intervals; CPU sensitivity and finite resource-integral comparisons. **Not demonstrated:** transfer to real LLM capacity; steady-state production sizing; HPA/KEDA benefit; ROI, energy saving or HA.

The observations motivate a deadline-aware admission boundary that coordinates provider budget, queue capacity and client deadlines. A reusable Alien6 component is a future engineering hypothesis. The current negative policy comparison does not demonstrate that an unimplemented library improves outcomes. A subsequent comparison would need identical traces/quotas/deadlines, complete qualification, useful-arrival outcomes and wasted-work accounting; no such new campaign is required to finish this publication.

**Disallowed formulations:** extra pods never help; global admission always improves reliability; CPU/network can never limit; emulator quota units equal actual model tokens; eventual HTTP200 equals a useful client result; three trial repetitions prove a universal law; prepared HPA/KEDA manifests are measured results.

## Primary method references and licensing

- Kubernetes resource management: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/ — resource requests/limits and CPU throttling.
- Kubernetes HPA: https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ — controller semantics only; no HPA outcome in this study.
- Ollama API and model controls: https://docs.ollama.com/api and https://docs.ollama.com/faq — raw version/digest/options in the archive override mutable model tags.
- Little, 1961, queueing relation: https://doi.org/10.1287/opre.9.3.383 — a theoretical context, not proof of stationarity or a fit to these short experiments.
- Apache-2.0 original code/docs; upstream images/tools/model weights retain their licenses; no model weights or customer deliverables bundled. See `THIRD_PARTY_NOTICES.md`.

References explain methods, not independent corroboration of this experiment’s numbers.

## Exact remote qualification observations

Source per block: `archives/BLOCK/control.json.gz`. Each row is one headroom repetition; transport timing, not model inference. The p95 latency gate is100ms and emission-lag gate50ms; every repetition must pass.

| Block | Repetition | Request denominator | p95 latency(ms) | p95 emission lag(ms) | Passed |
| --- | ---: | ---: | ---: | ---: | --- |
| v2-remote-network | 0 | 392 | 434.15 | 4.07 | False |
| v2-remote-network | 1 | 325 | 618.81 | 3.80 | False |
| v2-remote-network | 2 | 336 | 1061.10 | 3.97 | False |
| v2-remote-compressed-network | 0 | 392 | 99.08 | 3.83 | True |
| v2-remote-compressed-network | 1 | 325 | 129.04 | 4.03 | False |
| v2-remote-compressed-network | 2 | 336 | 182.82 | 4.06 | False |
| v2-remote-fixture-network | 0 | 392 | 118.12 | 4.11 | False |
| v2-remote-fixture-network | 1 | 325 | 108.86 | 4.04 | False |
| v2-remote-fixture-network | 2 | 336 | 135.36 | 4.00 | False |
