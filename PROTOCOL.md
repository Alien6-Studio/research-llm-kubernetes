# Kubernetes client capacity

Registered before outcome collection, 12 November 2026. Publication dates of the
articles remain historical; new experiments will be dated as article updates.
No conclusion or article title is a success criterion for this experiment.

## 2. Kubernetes client capacity

Question: how do replicas, a shared inference-service quota and admission policy
affect completed request throughput and user-visible waiting time?

Use a dedicated local kind cluster and kubeconfig; do not change the user's
current Kubernetes context or deploy into an existing application cluster.
Pin the node image and application image. Record host and container limits,
Kubernetes version and run timestamps. This is a local single-node mechanism
study, not a cloud provider capacity claim or a GPU inference benchmark.

Phase A uses an HTTP inference emulator with controlled latency and shared
quotas. Actual client API pods and actual HTTP load run on Kubernetes. Phase B
uses the selected local Ollama service and records actual model timings. Results
from an emulator and a real model are always labelled separately.

Initial matrix: replicas 1, 2, 4, 8; non-saturating upstream, saturating upstream,
and bursty load. Compare no client regulation with a shared admission budget;
then separately test autoscaling rather than mixing policies into replica tests.
Every admitted attempt, including retries, consumes the same upstream budget.

Use open-loop, seeded arrival schedules. Fix prompt/token-size and service-time
distributions before runs. Measure warm-up separately; execute each scenario
three times in randomised order, with a fixed measurement interval and bounded
drain period. Store offered arrivals, actual dispatches, completions, deadline
failures, 429 responses, queue waiting times and queue-depth samples.

Report completed **useful requests** per second, p50/p95/p99 end-to-end latency,
refusals, retries, work left at the deadline, CPU time and memory samples. Do not
report successful-attempt latency alone as user latency. Account for generator
scheduling lag and invalidate a run if the generator cannot offer its trace.
Replicas and resource requests remain fixed within each non-autoscaling trial.

Cost: record CPU-seconds, memory and actual inference tokens per completed useful
request. Financial cost is reported only when declared unit prices or an actual
bill exist, explicitly as an estimate or an observed charge. Local free API calls
must not be represented as zero infrastructure cost.

Calculate replicate-level variability; requests from one trial are correlated
and are not independent experimental replications. Retain failed runs and state
exclusions with reasons. Publish the exact executable that produced the data.

## Publication rule

Keep this article URL: `/fr/research/articles/mise-en-production-ia-kubernetes/`. Write a pedagogical account only after valid runs
exist: the initial question, the investigation, the observations, what they mean
and what remains uncertain. Change a title if the evidence does not support it.
Code and measured data go through the shared GitHub repository, not a ZIP or a
public customer-deliverable page. No client data or proprietary implementation
is used. New evidence changes the article; it does not justify cherry-picking runs.
