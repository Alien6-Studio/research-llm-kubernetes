# Prospective experimental protocol

This protocol and its executable plan must be committed to the dedicated research
branch before collecting V2 outcomes. Measurement timestamps remain actual
execution times. The historical article date does not date new experiments.
Historical source and archives are immutable inputs. The website and article
are outside this mission.

## Questions and falsifiable predictions

| Hypothesis | Primary comparison | Observation that would contradict the proposed mechanism |
| --- | --- | --- |
| H1: application replicas can increase capacity while inference has headroom | 1→2→4→8 workers, unlimited emulator, no prior admission; identical within-repetition arrivals and service times | Little or negative useful-throughput gain despite qualified generator and provider headroom; resource or queue observations may explain the result |
| H2: a shared quota changes the marginal value of workers | 1-versus-8 replica gain at unlimited versus 40,000 units/s; additional 20,000/80,000 controls and provider-only observations | A plateau that fails to move when only quota changes, or moves instead with CPU/generator capacity |
| H3: coordinated admission changes useful outcomes and failure locations | global leases versus no prior regulation within each identical replica/load arm | Fewer provider429 responses without better timely completions, or worsening waiting, expiry, resource use or variability |
| H4: selected mechanisms transfer to actual inference | directly characterized Ollama versus the same model behind 1/2/4/8 Kubernetes workers | A different limiting mechanism, scaling curve or quality/latency tradeoff; emulator numerical ceilings are not presumed to transfer |

Primary endpoint: useful arrival-cohort requests/s. Useful means HTTP200, the
task's checkable correct result, and final response within the deadline measured
from the planned arrival on the generator clock. Secondary endpoints include
useful completion-window throughput, useful fraction, latency distributions,
failure location, quota utilization, queue occupancy and resource consumption.
No conclusion or existing article title is a success criterion.

## Emulator matrix and pairing

`protocol/v2-emulator-plan.json` fixes every condition, order, seed, original
trace reference, compressed-input SHA256 and decoded workload SHA256.
`python -m v2.plan` verifies that an existing plan is not overwritten.
Order uses seed20261113; the original traces use seed20261112 plus repetition.
Unlimited and quota arms now use exactly the same request IDs as well as the
same arrival offsets, declared units, service times and task inputs.

| Block | Conditions | Trials |
| --- | --- | ---: |
| Generator qualification per resource envelope | 1/8 workers ×180/360 arrivals/s ×3 repetitions; zero service time, no binding quota | 12 at 4 CPU; 12 at 8 CPU |
| Primary | 1/2/4/8 workers × regular unlimited/regular40,000 quota/bursty40,000 quota × none/global admission ×3 | 72 |
| CPU sensitivity | fresh paired 4/8 CPU ×1/8 workers ×unlimited/40,000 quota ×none/global ×3; CPU order randomized within this block | 48 |
| Additional quota sensitivity | 20,000/80,000 units/s ×1/8 workers ×global admission ×3; compare with the primary40,000 arms | 12 |
| Provider-only control | bypass workers; unlimited/20,000/40,000/80,000 units/s ×3 | 12 |

The extra fresh4CPU sensitivity arms avoid equating resource differences with
collection order. An independent VM unable to supply8CPU cannot establish that
counterfactual; do not represent an8CPU cgroup allowance on a smaller host as8
available cores. Local and independently hosted blocks remain separate.

Each trace lasts13seconds:3seconds warmup and10seconds measurement, followed by
bounded drain. Deadlines are2seconds from planned arrival. Regular arrivals
are the retained180/s Poisson traces; bursts alternate2seconds180/s and2seconds18/s.
Declared units are1000/2200 with equal probability; service duration is0.14/0.22s
plus uniform±0.02s, with2% adding0.6s. Every worker has4 active slots and32 extra
queue places. Provider request ceiling is1000/s. No primary inference retry.
Warmup outcomes and resources are retained separately from measurement cohorts.

## Reservation and clock semantics

Only the provider owns the fixed one-second quota windows. Atomic reserve and
consume operations share one ledger. A reserved lease consumes request/unit
capacity immediately; consumption converts that lease without a second debit.
Expiry is min(reserve time+250ms, end of current window). Expired or explicitly
released unconsumed leases restore capacity; consumed work never does. Every
grant, denial, expiry, consumption and release is retained. Reconsumption fails.
An expired lease may be reacquired while a request still has time; all admission
and provider call attempts remain visible. No regulation directly consumes the
same ledger in a single operation. There is no second independent quota counter.

Global admission waits while holding a worker slot, within the same bounded
worker queue. This is a concrete policy under test, not a presumed optimal queue
discipline. Report shifted rejections and waiting rather than only provider429.
No extra queue policy is added to the primary matrix.

All durations use monotonic clocks. Each component records its clock domain;
absolute monotonic origins on different hosts are never subtracted. Network
messages carry remaining durations, which are approximate remote scheduling
budgets; the generator's planned-arrival deadline is the authoritative useful
outcome. Small transport delays can make remote budgets optimistic, never make a
late client response useful. Resource and component spans use local durations.

## Complete request accounting

Retain planned arrival, task launch, actual socket emission and client terminal
time; worker receipt, queue entry/exit, admission entry/exit, provider call and
reply; provider receipt, consumption and terminal response. Missing stages on
rejected/not-emitted requests are null with an explicit reason. Worker/provider
records are retrieved after drain even if the client already expired.

The generator independently schedules asynchronous request tasks; it does not
wait for a previous response before offering the next arrival. Non-emission,
late emission, refusal, transport error, late success and deadline expiry remain
in the original arrival denominator. Report offered, emitted, worker-accepted,
provider-consumed, correct and useful counts separately. Do not call an arbitrary
HTTP200 successful without checking the task result.

Report p50/p95/p99 on (a) every measurement-cohort client terminal outcome,
(b) emitted outcomes, and(c) useful outcomes; give each population size. Timeout
latencies are observed/censored client waiting times, not eventual completion
times. Also retain stage-span distributions, instant concurrency, queue depth,
provider requests and units per window. No latency includes a fabricated sample
for a request whose stage was never reached.

## Integrity, qualification and exclusion rules

Use the dedicated `a6-capacity-v2` kind cluster and kubeconfig, never the user's
current context. Pin the historical kind node and Python base-image digests.
Record code/driver/plan hashes, Git commit, application image ID, actual container
imageIDs, Kubernetes versions, resource limits and host capabilities.

Before every trial require no residual active work/queues, wait for old pods to
terminate, match ready nonterminating EndpointSlice targetUIDs to workerUIDs,
then settle2seconds. Reset role records and the quota ledger only while idle.
Archive continuous readiness/restart/containerUID observations, cgroup CPU,
throttling, pressure, memory and OOM readings. Sampling is evidence over observed
intervals, not proof that a transient event could never occur. Retain missing
samples and collection overhead. Do not stop unrelated user workloads.

A complete qualification block must have every planned ID exactly once,100%
actual emissions, emission-lag p95≤50ms,≥99% useful measurement-cohort responses
in every trial, exact serving membership, expected code and resource telemetry,
no observed restart/OOM and fully drained state. Apply the same integrity gates
to primary trials except that genuine service rejections/expiry are outcomes,
not reasons to exclude an arm. Any technically invalid trial excludes its entire
comparison block from causal interpretation. Preserve every trial, including
passing trials in a failed block. Do not replace a failed trial until it passes.
Execution defects require a new committed correction and separately named block.

## Analysis and confirmation size

The experimental units are the3 repetitions, not individual requests. Show all
replicate values and paired differences. Report means and descriptive95% Student
intervals at trial level (df2; critical4.3026527), explicitly conditional on a
normality assumption that n3 cannot verify. Do not interpret these as powered,
multiplicity-adjusted confirmatory hypothesis tests. Use raw paired curves,
negative observations and competing mechanisms alongside intervals.

Before any later confirmation, estimate paired standard deviations from the
exploratory repetitions. Provide a sample-size sensitivity calculation for an
illustrative5 requests/s change and5 percentage-point useful-fraction change,
80% power and two-sided alpha.05: ceil(((1.96+.841621)*SD/effect)^2), minimum8.
Also use the95% upper SD bound (df2 chi-square .05 quantile.1025866) to show how
uncertain n3 variance is. These are planning approximations, not guaranteed power
or a production SLO. A subsequent confirmation needs a separately frozen plan.

## Real-model pilot and confirmation

Use a separate local Ollama0.40.16 server, loopback11435, preserving11434.
NUM_PARALLEL4, MAX_QUEUE256, MAX_LOADED_MODELS1, CONTEXT_LENGTH8192; the pinned
Qwen digest, Q4_K_M and generation options are in `v2/workload.py`. Verify API
version, model digest, actual placement and loaded context. Record host hardware,
CPU/GPU/RAM, model loading, cache counts and model timings. Pay for no inference.

First characterize the direct model path with a separately identified pilot:
concurrency1/2/4,16 deterministic requests per condition. This is closed-loop
capacity characterization, not a latency experiment under open-loop saturation.
Freeze low/near/high arrival rates at.25/.85/1.5 times the highest observed
aggregate completion rate, rounded to.01requests/s (minimum.01). Retain the
pilot, commit its derived rate plan before confirmation, and do not revise
rates after seeing confirmation. Real traces use the same controlled short/long
HTTP-status prompts and actual input/output/cache tokens, not declared units.

Then measure direct access and1/2/4/8 workers at each frozen arrival rate, three
repetitions. Warmup5s, measurement20s, deadline10s; no artificial binding quota.
Pair exact arrivals and prompts within each repetition/profile. Calibrate the
generator for the highest chosen rate. Retain model prefill/eval/load/total
durations where available, unknown server waiting as unknown, and residual
timing rather than asserting a decomposition the API cannot expose. Warm caches
and repetitive classification are strong limitations on transfer to other tasks.
Started inference is drained even after client expiry; measure wasted late work.

If the model, memory, direct path or hardware cannot be qualified, retain the
precise failed controls and stop that phase. Never substitute emulator outcomes
for real-model results.

The executable real-model extension records a partial native pilot after each
completed condition, including failed warmup or task outcomes. It adds12 complete
model-path generator controls:1/8 workers × highest frozen rate/twice that rate
×3 repetitions, using the same prompt-sized payloads with zero emulator duration.
These must qualify before the45 real-model trials (direct plus1/2/4/8 workers,
three rates, three repetitions). Direct reference calls are allowed to drain
after the client's deadline while retaining the timeout as the client outcome.
For sparse real-model traffic, report actual serving UID coverage and require
all observed UIDs to belong to the verified ready EndpointSlice set. Random
low-volume traffic need not hit every ready replica; do not equate the ready
replica count with the number that actually performed inference.

## Independently hosted CPU and real-model continuation

Both complete local emulator qualifications fail, and real-model client dispatch
failures are already observed after a passing low-rate idle-emulator qualification.
The local real-model block will still finish and be excluded in full. These
observations do not identify a GPU, VM timer, scheduler, network or generator
concurrency cause. A passing subset will not replace the excluded block.

Continue the registered4/8CPU controls on a new isolated Ubuntu24.04 LXD VM
with8actual vCPU and16GiB RAM on the user-provided Linux host. Keep the kind node
at8GiB and vary only its CPU allowance according to the existing frozen plan.
Both complete4/8CPU qualification blocks must pass before the48 paired trials.
Before this continuation, strengthen the executable's trial isolation: record
and enforce trial identities at workers, leases and inference consumption, check
all roles idle before any reset, and stop after an unsuccessful drain. This is a
prospective integrity correction, not evidence of contamination in earlier
blocks. Earlier source checkpoints and observations remain separate and intact.
Retain VM image fingerprint, kernel/Docker/Kubernetes versions, source/image
identity and VM CPU/steal/memory observations. This is not a dedicated bare-metal
host; unrelated hypervisor workloads remain a limit.

The real-model continuation places clients and Kubernetes on that VM while the
same dedicated native Mac Ollama server performs inference. A private SSH reverse
tunnel terminates on VM loopback; a relay binds only the VM's own Docker bridge,
not a public interface. Direct reference and worker paths use the same relay.
Freeze the bridge endpoint in a separate derived plan before collection, keeping
the original rates, byte-identical traces, task, generation parameters and45
conditions. Repeat the entire12-trial qualification and45-trial model block.

Before model confirmation, also qualify the SSH/API transport with three open-loop
headroom traces against `/api/show`: local model metadata, no generation, with
prompt-sized padding. Record planned/emitted/terminal times, exact byte counts,
response hashes/statuses and all failures; require every response successful,
every request emitted, p95 emission lag≤50ms and p95 latency≤100ms. This is a
transport control and must never be reported as inference throughput. It does
not remove the need for per-trial emission checks during actual inference.

Native inference CPU/RSS, thermal indications and loaded-model state are sampled
on the Mac, bracketed by explicit per-trial start/stop RPCs through a second
loopback SSH tunnel. VM observations remain distinct. No Mac and VM monotonic
origins are subtracted. Moving environments tests a different source of
interference; it cannot alone diagnose the earlier local failure's cause.
For subsequent real-model confirmation planning, use an illustrative throughput
effect of20% of the frozen native-pilot capacity (0.9378873 requests/s), alongside
the5-percentage-point useful-fraction effect. The3-repetition model blocks remain
exploratory rate-frozen comparisons, not powered confirmatory tests.

## Separate autoscaling design and resource accounting

### Transport continuation after the first remote control

The complete uncompressed SSH control failed the unchanged100ms latency gate
in all three repetitions (p95:434,619,1061ms), despite timely emission and no HTTP
failures. Its26,490-byte metadata responses are larger than classification
responses. Ten sequential diagnostic requests to the same remote server gave
roughly21ms median for `/api/version` and `/api/ps`; ten local `/api/show` calls
gave roughly1ms median. These diagnostics do not isolate a single network cause.
No model comparison followed that failed gate.

Register a separate `v2-remote-compressed` continuation using SSH's `-C` transport
compression, with the same endpoints, payloads, traces, thresholds and ordering.
Retain the entire failed control. Require all three metadata headroom controls,
the complete12-trial qualification and every actual trial's emission/drain gates.
Compression adds CPU work and this path is a distinct experimental environment;
its results will not be pooled with the earlier local model block. A passing
metadata control is necessary here, not proof that transport never limits actual
inference. Do not weaken a threshold to obtain a qualified model block.

## Separate autoscaling design and resource accounting

Keep the historical repository controller labelled prototype. Prepare fixed
pods, CPU HPA, an external-metric HPA/KEDA and a provider-capacity-aware policy
as a separate study. Use15-minute traces with low3minutes, high5minutes and
recovery7minutes;3 randomized paired repetitions, explicit controller versions,
polling periods, readiness and downscale stabilization. Record decisions,
actual-ready replicas and transition/oscillation/throughput trajectories. Only
run policies whose metrics are verified to be available. Missing controllers or
metrics are a documented phase limitation, not an imitated HPA result.

HPA's default control period is15seconds and its default downscale stabilization
is300seconds ([Kubernetes](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)).
KEDA's polling/cooldown and HPA behavior have distinct roles
([KEDA](https://keda.sh/docs/2.21/reference/scaledobject-spec/)). These references
motivate a separate longer horizon; the original30-second prototype cannot stand
in for them.

Integrate observed CPU time, memory byte-seconds and model tokens over clearly
labelled complete observation intervals. Divide by useful completions over that
same interval, showing missing telemetry rather than treating it as zero. Shared
node/control-plane overhead and local GPU interference are not silently assigned
to individual requests. Record physical resource use first. No monetary price or
ROI is inferred without a verified tariff or bill.
