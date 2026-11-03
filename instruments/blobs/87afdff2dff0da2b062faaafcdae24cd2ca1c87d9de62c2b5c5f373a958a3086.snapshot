# Shared native admission on the retained Ollama workload

We study whether one shared in-flight limit changes useful client outcomes on
the existing CPU-hosted Ollama task. This is a new paired experiment. The earlier
45 observations remain exploratory context and are not pooled with these data.
The intervention is a global capacity cap, not a claim that coordination alone
or a generally applicable library has already been validated.

## Fixed capacity selection

Use the original archived 52-response calibration without another pilot. For
each tested concurrency 1/2/4, compute per-request HTTP elapsed time after the
pilot semaphore was acquired. Select the largest concurrency whose empirical
p95 is at most ten seconds. If none qualifies, select the smallest tested
concurrency and record `pilot_deadline_feasible=false`.

The retained sixteen-request groups have p95 values of approximately 11.098,
28.670 and 49.328 seconds. The rule therefore selects K=1, with no SLO guarantee.
Eight of sixteen concurrency-one requests finished within ten seconds. With
sixteen observations, the pinned percentile implementation makes p95 equal to
the observed maximum. This small closed-loop sample sets a treatment parameter;
it does not estimate production capacity or show that admission can make every
request meet its deadline. Native parallelism remains four in both arms.

## Paired design

Use 1/8 Kubernetes workers, near/high load, bypass/global admission and three
retained trace repetitions: 24 trials in twelve pairs. Each pair shares the same
compressed trace, request contents, model options and resources. Shuffle pairs
and the two arm orders using seed 2026111501 before collection. Reset native and
proxy processes and complete four common technical warmup responses before
every trial. Refresh transport and generator controls for the new instrument.

Keep 335-second arrival windows, the ten-second client deadline, 120-second
model-call and 180-second native-forwarding limits, and the 900-second collector
budget. The old per-pod four-slot semaphore and nonbinding quota remain fixed.
At K=1, even one worker changes its total concurrency budget; it is not a pure
overhead control for the global mechanism.

Both arms use the same instrumented native path. Bypass forwards without the
shared cap; global immediately rejects a request when all K permits are held.
No new waiting queue or request retry is added. Rejection is an unsuccessful
client outcome. A permit follows the associated native generation until its
successful terminal response, independently of client expiry. An uncertain
native outcome quarantines capacity until certified process reset; a timeout
must not silently return the permit while native work may remain.

## Evidence and endpoints

Bind the unchanged BBA role/native instrument separately from the extended study
source. Archive admission attempts for every possible inference API call,
including rejections, and bind each accepted attempt to its native sequence.
Check source identities, epochs, counters, request-payload multisets and permit
lifetimes from the retained ledger. Rooted termination and fresh-epoch evidence
are required between trials. Source, generation, coverage or lifecycle defects
stop collection; neither an overload outcome nor a late model response is
relabeled as a successful native certificate.

The primary endpoint is the number of correct client responses within ten
seconds divided by **all scheduled measurement arrivals**. Refused, unemitted
and expired requests stay in that denominator. Native uncertainty can coexist
with an observed client cohort, but native resource lifetimes remain censored.
Do not compare only the resource-surviving subset.

Recalculate each pair's global-minus-bypass primary difference and expose all
three repetition-level differences per replica/load condition. Any mean is
descriptive across those three trial pairs, not pooled request-level evidence.
Do not report a confirmatory interval, significance test or general library
effect. Keep incomplete pairs and technical failures visible, without selecting
favorable repetitions or combining these trials with the earlier 45.

The native ledger records completion of non-streaming generation HTTP calls.
The API's terminal `done=true` field supports this completion criterion; it does
not measure host-wide idleness ([Ollama generation API](https://docs.ollama.com/api/generate)).
Report summed native HTTP and held-permit durations only over the complete trial
trace, using the proxy's single clock. These durations include waiting and are
not CPU time or per-client wasted work. If a native lifetime is uncertain, keep
its resource duration censored. Refresh controls are hash-bound to the new study
and bracketed by the same empty proxy epoch before the first comparison.
