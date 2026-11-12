# Validity gates before interpreting capacity measurements

Status: the first 21 trials are a **pilot**, not a completed comparison. All are
retained, including 9 that failed the initial checks. After replica reductions,
requests still reached terminating old pods. The complete pilot is excluded from
replica-effect conclusions, including its 12 trials that passed the first checks.
A corrected experiment must use a new directory and a new protected protocol tag.

## Claims and competing explanations

| Proposed claim | Alternative explanation to rule out | Required control |
| --- | --- | --- |
| Shared inference capacity limits throughput | Load generator or VM CPU is saturated | Fast-path calibration, dispatch lag, per-role and node CPU/throttling, resource-envelope comparison |
| A quota limits useful completions | Provider CPU, queues, or network is the limit | Remove or change only the quota, retaining the same trace, service times, and resource envelope |
| Adding clients does not help under the quota | Traffic reaches stale or only one worker | Settled EndpointSlices, old pods terminated, exact observed worker membership |
| Admission policy changes failures or waiting | Different arrivals or retry work | Paired deterministic traces; record every attempt, queue wait, rejection, and deadline |
| A local Ollama limit behaves like an emulator quota | Shared GPU, model loading, cache, or host pressure | Separate real-model phase, fixed server parallelism/model/context, warm-up, placement and cache/timing records, native-path baseline |
| A result applies beyond this workstation | Mac, Docker Desktop VM, or single-node artifact | Repeat the essential contrasts on an independent Linux host; retain local-only wording until this passes |

## Prospective qualification sequence

1. **Stabilize routing before load.** Wait for old worker pods to terminate and
   for ready, non-terminating Service endpoints to match the configured pods.
   Allow a fixed settling interval. Record the membership and compare it with
   actual responding pods. Do not count a rolling transition as a replica trial.
2. **Prove generator headroom.** Use the same HTTP route and instrumentation with
   zero simulated inference time and no quota. Test the intended arrival rate
   and a higher calibration rate, in three complete repetitions. Retain all
   dispatch delays and response times. The intended-rate control requires at
   least 99% useful responses, p95 dispatch lag <=50ms, and no missing IDs.
   A failed calibration prevents interpreting a primary plateau as upstream
   capacity. Fix the harness or preregister a lower rate before a new campaign.
3. **Observe resources rather than assume isolation.** Retain host load/memory
   observations, Docker VM capacity, node-container CPU/memory limits, per-role
   cgroup CPU time and throttled time, pressure/OOM counters where available,
   pod restarts, and scheduling lag throughout each trial. Missing telemetry is
   explicitly missing, not evidence of headroom. Do not stop unrelated workloads.
4. **Run counterfactuals.** Compare binding and non-binding quotas on identical
   traces, and repeat the essential 1-versus-8-client contrasts with the node
   CPU allowance increased from 4 to 8 while other parameters remain fixed.
   Change quota independently as a second control. If a plateau moves with CPU
   resources or the fast-path ceiling, its cause is not identified as quota alone.
   Keep the full planned matrix; do not retain only successful-looking arms.
5. **Reproduce the mechanism independently.** Use a separate Linux environment
   for the essential unlimited-versus-quota and 1-versus-8-client contrasts.
   Match the executable and arrival traces, record resource differences, and
   report disagreement. A second hosted VM does not establish bare-metal or
   cloud-production performance; it tests a different source of local interference.

Prospective parameters: calibration uses 180 and 360 requests/second, 1 and 8
workers, 3 repetitions, 3 seconds warm-up and 10 seconds measurement, zero
emulator service time and no binding quota (12 trials). Use seed 20261112 and
randomize trial order. Endpoint settling is 2 seconds; host sampling is every
2 seconds, in-pod sampling every 0.5 seconds.

Resource sensitivity repeats 1/8 workers x unlimited/40000-unit quota x none/global
admission x 3 repetitions at an 8-CPU node allowance, compared with the matching
4-CPU primary arms (24 additional trials). Quota sensitivity uses 20000/80000
units per second x 1/8 workers x global admission x 3 repetitions, paired with
the existing 40000-unit primary arms (12 additional trials). Other distributions,
limits, and traces remain the primary protocol's settings. Independent Linux
reproduction covers 1/8 workers x unlimited/40000-unit quota x no admission x
3 repetitions (12 trials), with its actual host resources reported.

Freeze these specifications and the executable in a new protocol tag before the
qualification and confirmatory runs. No new confirmatory outcomes exist yet.

## Interpretation and exclusions

Generator lag above the registered limit, missing IDs, executable mismatch,
incorrect worker membership, a restart/OOM, or unsatisfied resource controls are
reported with the affected raw trial. Do not silently retry an individual failed
trial until it passes. An identified execution defect requires a documented
correction and a new complete comparison block.

Report all planned arms, valid replication counts, and every exclusion. Three
repetitions show observed variability; they do not establish broad statistical
power or hardware independence. Unknown host/GPU interference remains a limit.
CPU throttling can be a real local bottleneck and must not be relabelled as an
inference quota. Emulator declared units are not actual model tokens.

A conclusion can be a measured local mechanism, a resource-sensitive observation,
or an unresolved result. It is acceptable for this experiment not to support the
article's original title. General capacity, cloud cost, production benefit, and
semantic quality claims require their own evidence.

## Primary technical references

- [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/): CPU requests and limits have different roles; throttling is a possible execution constraint.
- [Docker Desktop VM resources](https://docs.docker.com/desktop/settings-and-maintenance/settings/): VM CPU, memory, and resource-saving behavior are part of the environment.
- [Ollama FAQ](https://docs.ollama.com/faq): model placement, context, parallelism, and memory are relevant execution settings.
