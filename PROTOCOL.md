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

## Parameters fixed before load collection

The primary emulator matrix has 72 trials: 4 replica counts x 3 load/quota
profiles x 2 admission policies x 3 repetitions. Each trial has 3 seconds of
warm-up, 10 seconds of measurement, and bounded request deadlines of 2 seconds.
The generator offers an open-loop Poisson trace at 180 requests/second; the
bursty profile alternates 2 seconds at 180/s and 2 seconds at 18/s. All arms
within a repetition receive the same trace. The seed is 20261112 + repetition.

Each worker has 4 active slots and 32 additional queue places. Requests declare
1000 or 2200 quota units, with equal probability. Emulator service times are
0.14 or 0.22 seconds respectively, plus uniform noise of +/-0.02 seconds; 2% of
requests add 0.6 seconds. The saturating upstream budget is 40000 declared units
per fixed one-second wall-clock window. The request ceiling is 1000/s. The
non-saturating condition uses a token ceiling of 1e9 units/s. Primary trials do
not retry. A separate 2-pod comparison uses 1 or 2 attempts, three repetitions.

Invalidate a trial if generator scheduling-lag p95 exceeds 50ms, request IDs are
missing, the executable differs, or traffic misses a configured replica. Retain
invalid raw trials and reasons. Report window completions separately from the
cohort of measurement arrivals completed before their deadline.

Autoscaling uses an explicit prototype controller in this repository, not HPA
or KEDA: poll every 2 seconds, double replicas when queue depth exceeds 8, cap at
8. Compare queue-only scaling with a budget-aware rule that requires observed
upstream quota utilisation below 95% over the preceding sample interval. Start
at 1 replica, saturating upstream, global admission, 30-second measurement, three
repetitions of each policy. Report every decision and the actual replica changes.

The real-model phase uses a dedicated local Ollama 0.40.16 server on loopback
port 11435, NUM_PARALLEL=4, MAX_QUEUE=256, MAX_LOADED_MODELS=1, CONTEXT_LENGTH=8192.
It shares installed weights but does not change the existing port11434 service.
Use qwen3.6:latest with digest
07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522,
Q4_K_M, temperature0, seed20261112, thinking disabled, output limit32 tokens.
The task is a controlled HTTP-status classification with a checkable JSON label;
it is not a representative business task. Offer 4 requests/second, use 5 seconds
of warm-up, 20 seconds of measurement and 10-second deadlines. Test 1,2,4,8
replicas three times. No artificial quota binds this phase. Record actual prompt,
output and cached token counts where reported. This tests a shared local model
server, not commercial API quotas or cloud pricing.
