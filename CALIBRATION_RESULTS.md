# Calibration results

Separately identified complete calibration blocks; no pooled causal effect, throughput-capacity or real-LLM inference claim.

P95 dispatch lag uses the complete 13-second trace. Useful fractions use the ten-second measurement window. Three repetitions remain separate; no individual failed trial is regenerated into a passing block.

| Cohort | Workers | Planned requests/s | P95 dispatch lag, repetition 0/1/2 (ms) | Valid trials |
| --- | ---: | ---: | --- | ---: |
| local-v2 | 1 | 180 | 3.4 / 3.6 / 9.0 | 3/3 |
| local-v2 | 1 | 360 | 4.9 / 148.9 / 3.8 | 2/3 |
| local-v2 | 8 | 180 | 7.9 / 3.8 / 17.1 | 3/3 |
| local-v2 | 8 | 360 | 8.6 / 61.2 / 4.2 | 2/3 |
| local-v3 | 1 | 180 | 5.2 / 4.5 / 4.7 | 3/3 |
| local-v3 | 1 | 360 | 67.7 / 20.7 / 60.6 | 1/3 |
| local-v3 | 8 | 180 | 4.8 / 4.5 / 7.3 | 3/3 |
| local-v3 | 8 | 360 | 13.8 / 25.6 / 32.0 | 3/3 |
| linux-v3 | 1 | 180 | 0.2 / 0.3 / 0.2 | 3/3 |
| linux-v3 | 1 | 360 | 0.5 / 0.4 / 0.4 | 3/3 |
| linux-v3 | 8 | 180 | 0.3 / 0.3 / 0.2 | 3/3 |
| linux-v3 | 8 | 360 | 1.0 / 0.8 / 0.6 | 3/3 |

## Interpretation

A high response-success rate does not establish that planned load was dispatched on schedule. The original clock and later monotonic-clock cohorts are separate observations. A correction does not retrospectively identify the cause of an earlier failure.

Resource windows report per-role mean CPU and cgroup throttling. Heartbeat events are exploratory pause diagnostics with overlapping windows and bounded retention; they do not identify a VM, scheduler, CPU or clock cause on their own. No independent human or operational causal adjudication is implied.

Any unqualified complete block blocks confirmatory interpretation under this protocol. Lowering an arrival rate or changing the harness requires a new prospective specification and complete block. Independent Linux results remain conditional on that hosted VM and its recorded resources. A passing block does not establish bare-metal, real-model or production capacity.
