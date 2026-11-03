# Separate autoscaling study — registered design, not measured results

The historical `run.py autoscale` controller is a repository prototype. It is
neither Kubernetes HPA nor KEDA. The fixed-replica V2 trials do not compare any
autoscaler. Do not use their short windows to rank controllers.

## Questions and experimental units

Compare four arms: fixed eight workers, CPU HPA, backlog-based KEDA, and an
explicit provider-aware experimental controller. Test both provider headroom
and a 40,000-declared-unit/s quota. Use three paired arrival seeds per scenario,
randomized arm order:24 trials, each 15 minutes plus complete drain. Each trial,
not each request or autoscaler polling tick, is one experimental unit.

The arrival process has three phases:10 requests/s for 180 seconds,
180 requests/s for 300 seconds, then 10 requests/s for 420 seconds. Generate one
Poisson trace per seed (20261120–20261122), reuse identical request bytes and
service/unit distributions across all arms, and publish the traces and hashes
before collection. Use the V2 emulator distribution without replacing it with
an average. The 10-minute transition history must not leak from a preceding arm.

## Arms and proposed settings

| Arm | Initial/minimum/maximum | Decision input | Proposed behavior |
| --- | --- | --- | --- |
| Fixed |8/8/8 | None | No autoscaler |
| CPU HPA |1/1/8 | CPU relative to 100m requests |80% utilization target;15s sync;300s downscale stabilization |
| Backlog KEDA |1/1/8 | Pending client requests |20 requests per desired worker;15s polling; HPA300s downscale stabilization |
| Provider-aware prototype |1/1/8 | Deadline-feasible backlog and observed provider headroom |15s decisions; cap proposed replicas by both provider capacity and measured per-worker capacity;300s downscale stabilization |

Pin Kubernetes 1.36.1, kind 0.32.0, metrics-server 0.8.1 and KEDA2.21.0 only after
checking compatibility and recording actual image digests. These are proposed
controller versions, not installed or observed versions. Retain all effective
flags, manifests and live controller configurations. Keep the same worker
resources, queues, deadline and admission implementation across arms. Use a new
isolated cluster/namespace; do not install controllers into a running fixed-arm
campaign.

The provider-aware controller is not implemented or qualified here. Its capacity
estimator needs a separate pilot: freeze the estimator, averaging horizon and
minimum useful effect before any arm comparison. No manually selected replica
trajectory can substitute for that controller.

## External metric contract and qualification

`alien6_pending_requests` counts accepted requests without a terminal outcome.
Export it once per second from the provider-independent request ledger, with
trial identity, timestamp and reset semantics. A stale (>5s), missing, negative
or duplicated series is an invalid metric, never a zero. Prove conservation
against individual requests through arrivals, completions, expiry and reset.
The external metric must include work waiting outside individual worker queues;
otherwise scale-out can hide backlog by moving it.

The KEDA template is a configuration specimen. Prometheus, the exporter, metrics
server and provider-aware controller have not been provisioned or validated.
First qualify their measurements under known injected backlog and CPU load,
then freeze their image identities and resulting traces in a new source commit.
Do not start the 24-trial comparison until every arm can consume valid metrics.

## Measurement and exclusion

Retain scheduled/emitted/terminal requests and the V2 validity gates. Sample
desired/current/ready replicas, HPA status/recommendations/conditions, KEDA
activation/errors, pod creation/readiness/termination and exact serving UIDs
every second. Keep controller decisions and metrics at their own observation
times. Retain missing samples and events; do not interpolate them as successful
decisions. Observe time to first scale-out, time to stable ready capacity,
replica-seconds, reversals and time below a useful-service target. Report phase
trajectories and phase-specific useful throughput, deadlines and queueing.

Qualify the generator at twice the highest offered rate throughout a complete
15-minute trace. Require idle/drained roles, a reset controller history, no old
terminating pods, and a common warmed provider before every arm. Apply exclusions
to complete randomized blocks. A controller that makes an inefficient decision
is an outcome; missing/stale metrics or a failed generator are validity failures.

Three repetitions are exploratory. Report paired differences and uncertainty;
choose a new sample size against a predeclared useful-throughput/deadline effect
before powered confirmation. Do not rank controllers using overlapping point
estimates alone. CPU, memory, control-plane overhead and inference resources
remain separate physical measurements; no ROI is inferred.

## Technical basis

[Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
documents the default 15s reconciliation and 300s downscale stabilization.
[KEDA ScaledObject](https://keda.sh/docs/2.21/reference/scaledobject-spec/) distinguishes
polling/cooldown from HPA behavior. The 7-minute recovery phase allows observation
beyond the downscale window. Actual controller behavior still needs collection.
