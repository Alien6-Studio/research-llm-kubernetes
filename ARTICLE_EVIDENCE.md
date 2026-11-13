# Evidence for the future article

This is a writing brief, not a modification of the Alien 6 site. The current
article already makes a conditional claim about provider headroom and reports
the earlier essential contrasts. Keep that condition; replace idealized capacity
language with the exact populations and limits below after editorial review.

## Statements supported within the experiment

| Future statement | Evidence | Necessary qualification |
| --- | --- | --- |
| More workers can help when the inference dependency has headroom. |72-trial matrix:1/2/4/8 workers give 18.87/39.63/76.27/170.73 timely cohort outcomes/s without prior admission. | Three workload seeds;10 second arrival cohorts plus drain; finite offered load, single-node kind. No universal per-pod rate. |
| A shared provider limit changes the value of scaling application replicas. |1→8 workers adds 151.87/s with headroom versus 5.87/s at 40k quota. Provider-only 20k/40k/80k controls move to 13.17/25.73/49.80/s. | Declared emulator units, not model tokens or real-provider tariffs. |
| The quota explanation survives meaningful competing controls here. | Actual-emission qualification, provider bypass, quota manipulation and 48 paired CPU controls. | CPU8−4 at eight quota workers:+0.60/s [−1.77,2.97]; uncertainty does not establish equivalence. Shared VM and network effects are not universally excluded. |
| Atomic admission accounting does not guarantee a useful client result. | Global policy uses about 99% of quota, but 487/762 consumed cohort requests miss timely client success. | This four-active-slot/32-queue policy waits without deadline-feasibility scheduling. Do not generalize to all admission policies. |
| Refusing less at the provider can still worsen the experience. | Global leases reduce the useful rate from 25.47 to 9.17/s at eight workers; p95 terminal waiting reaches the 2 second deadline. | Report displaced queue refusals and expirations, not just fewer provider 429s. |

## A pedagogical narrative grounded in the data

Start with one request's journey: arrival, worker queue, permission to consume
shared capacity, provider work, and the client's deadline. A correct answer
arriving after that deadline no longer helps the waiting client. Then show the
replica plot: capacity grows while the dependency has headroom. Change only the
quota and show that the outcome moves. Finally show that a correctly implemented
lease can authorize work too late.

Use the median non-tail consumed-but-unsuccessful request as a timing example:
1.810s queue +0.140s admission +0.147s provider call, beyond a 2s client deadline.
Explain that this is a post hoc illustration, and separately give the 487/762
aggregate count. Do not add separate p95 values to invent a typical request.

Suggested figures, all generated from archives:

- [Replicas and admission](analysis/v2-study/replicas-and-admission.svg): three
  profiles, all three workload observations visible.
- [Quota counterfactual](analysis/v2-study/quota-counterfactual.svg): change the
  suspected constraint and observe its movement.
- [CPU counterfactual](analysis/v2-study/cpu-counterfactual.svg): paired effects
  and wide uncertainty, rather than a claim of CPU equivalence.
- [Quota consumption versus timely outcomes](analysis/v2-study/deadline-and-quota-consumption.svg):
  distinguish doing work from delivering a useful result.

## Statements that are not established

Do not claim that extra pods are generally useless, that global admission always
helps, that a larger queue is better, or that CPU can never be the constraint.
Do not call finite-cohort results sustainable production capacity. The experiment
does not locate a diminishing-return knee beyond eight workers, compare HA or
reliability benefits, calculate ROI, or rank HPA against KEDA.

Actual Qwen generations exist, but the local 45-trial block is excluded in full
and the independent continuation fails its transport gate. The native pilot's
4.689/s rate selects load levels; it is not a qualified comparison of replicas.
Therefore the emulator mechanisms are **not yet experimentally transferred to a
real-model capacity comparison**. Keep that question open in any future article.

The new negative admission result cautions against a blanket recommendation;
it does not contradict a blanket claim in the existing article, which already
states conditions and marks further experiments pending. Keep old observations,
new qualified results and unresolved questions distinct. Link to the public raw
evidence and executable analysis, without publishing customer deliverables.
