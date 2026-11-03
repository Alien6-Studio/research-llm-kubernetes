# Audit of the retained Kubernetes experiments

Scope: the original `service.py`, `run.py`, `environment.py`, tests, protocols,
analysis scripts, frozen traces, manifests and complete archived cohorts at
`31bdb1fd103487fe61181f91dedaa8d9862ec16b`. No historical observation or executable
is changed by the V2 work. All new measurements belong to separate cohorts.

## Reproduced observations

`scripts/verify_evidence.py` verifies 108 published evidence files. Both historical
analysis programs reproduce their committed output exactly. All ten original
tests pass with loopback networking enabled. A sandbox denial of socket binding
was an execution-permission failure, not a failed scientific control.

The first 21 trials remain an excluded pilot. The complete local calibration
blocks each have ten passing and two failing trials; neither establishes local
headroom. The independently hosted Linux calibration passes all twelve controls.
Its twelve subsequent contrasts retain the same three paired workloads and all
failures. Unlimited one-worker event throughput is 19.7/20.6/20.3 requests/s;
eight-worker throughput is 165.5/161.5/158.9. Under the 40,000-unit ceiling the
corresponding values are 21.0/20.2/20.4 and 24.8/24.3/24.1.

Those observations support a conditional emulator mechanism. They do not
complete the 72-arm matrix, establish statistical power, reproduce a capacity
effect across hosts, or measure a real LLM. Their reporting already distinguishes
completion-window throughput from successful arrivals followed through drain.

## Confirmed defects and measurement gaps

| Area | Finding | Consequence and V2 correction |
| --- | --- | --- |
| Admission | `WINDOWS['admission']` and `WINDOWS['provider']` are independent ledgers. The former returns permission without an identifier, expiry or atomic consumption. | An admission just before a boundary can be consumed in the next provider window; a second admission in that new window can subsequently fail. A grant never followed by inference also occupies the admission counter. This is a preliminary check, not a coordinated reservation. V2 uses one provider-owned lease ledger. |
| Scope of that defect | All twelve archived Linux contrasts use `policy='none'`. | Their observed no-admission throughput is not invalidated by a reservation mechanism they did not exercise. No historical global-admission benefit was demonstrated. |
| Generator | An open-loop scheduler submits work to a 512-thread executor. Launch time is taken when its task starts, before connection establishment and socket transmission. Every eventual task remains recorded, including expired tasks. | Task-start lag is useful evidence but is not an actual emission timestamp. The bounded executor can defer dispatch under sufficient concurrency. V2 schedules asynchronous tasks and records successful socket writes, late emission and non-emission explicitly. |
| Deadlines | Client-to-worker deadline values are absolute monotonic times. The code checks matching time-namespace inode numbers. | This is conditional on the documented single-kernel kind setup; matching inode numbers alone cannot synchronize separate hosts. V2 exchanges remaining durations and retains local clock-domain-labelled events. Planned-arrival deadlines are judged solely on the generator clock. |
| Errors | Worker transport timeouts reach the generic HTTP500 handler without the normal attempt and stage fields. | The twelve contrasts retain these failures, but missing attempt counts and stage timing are unknown. V2 retains separate client, worker and provider records even after client expiry. |
| Routing | Scaling waits for old pods to disappear and for ready nonterminating EndpointSlice IPs to converge. Final validation checks only the number of responding pod names. | The convergence step is valuable. A count alone does not prove exact membership. V2 checks expected pod UIDs against EndpointSlice target UIDs, responding UIDs and continuous Kubernetes observations. |
| Restarts | Cgroup CPU, pressure and OOM observations are sampled. Continuous pod restart counts were not archived. | No sampled reset or OOM does not prove no restart. V2 records pod/container identities, restart counts, readiness and states throughout each trial. |
| Resources | Provider, controller and workers share a node. CPU limits differ by role; telemetry is sampled. Registered CPU and quota counterfactuals were not completed. | A plateau cannot generally be assigned to quota alone. V2 separates fast-path qualification, provider-only controls, worker scaling, quota changes and randomized CPU-envelope controls. |
| Statistics | Three repetitions use three seeded traces on one VM. Request counts are large but correlated within a trial. | Experimental sample size is three paired repetitions, not thousands of independent requests. V2 reports paired effects and uncertainty with explicit assumptions, and a prospective confirmation-size calculation. |
| Autoscaling | The two-second queue/budget controller is repository code. | It is not HPA or KEDA. No retained completed autoscaling cohort establishes its benefit. A separate long-horizon design is required. |
| Real inference and costs | The Ollama phase was specified but not collected. Emulator units are declared consumption, not model tokens. | No real-model throughput, GPU efficiency, monetary cost or ROI is established. V2 must collect actual inference records and retain unknown quantities as unknown. |

## Corrections and interpretation boundary

The V2 harness is a new asynchronous implementation. Its results must not be
pooled with historical trials or used to claim that a particular historical
failure was caused by threads, clocks, the Mac or the VM. Historical source and
data remain byte-for-byte available. The original workload distributions, queue
capacity, four active slots per worker and principal matrix are retained.

A lease reserves request and unit capacity atomically in one provider window.
Consumption converts that same reservation exactly once. Unconsumed leases
expire at the earlier of 250 ms or the window boundary and release capacity;
explicit cancellation can also release them. Consumed work is never refunded,
including failed or late work. Boundary races are visible refusals and may lead
to a new lease, without silently repeating an executed inference.

Qualification must pass as a complete block before capacity interpretation.
Technically invalid blocks are preserved and excluded in full. A high success
fraction alone does not demonstrate that the generator offered its planned load.

## Reproduction

```bash
python -m unittest discover -p 'test_*.py' -v
python scripts/verify_evidence.py
python scripts/analyze_calibration.py --check
python scripts/analyze_focused.py --check
```

New protocol and controls: [PROTOCOL_V2.md](PROTOCOL_V2.md), `v2/`.
