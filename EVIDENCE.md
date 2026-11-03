# Evidence index

The study separates qualified emulator interventions, actual-model observations
and excluded collections. A reproducible calculation does not repair a failed
collection or establish a causal effect.

[Results](RESULTS.md) · [Validity boundary](VALIDITY.md) ·
[Protocol](PROTOCOL.md) · [Article evidence](ARTICLE_EVIDENCE.md)

## Native CPU baseline

All 45 frozen conditions are visible. Forty-four observations have complete
native-completion certificates; one original direct-model observation retains
uncertain native completion. All 28 observations in the reset-per-trial suffix
pass their individual integrity, resource and native-completion checks. The
original 17-observation block remains unqualified.

| Evidence | Public location | What is verified |
| --- | --- | --- |
| Complete observation table | [JSON](analysis/v2-ollama-fra6s004-cpu-continuation/results.json), [CSV](analysis/v2-ollama-fra6s004-cpu-continuation/trials.csv) | Every planned condition, client-arrival denominator, epoch, raw SHA and separate completion/resource status |
| Frozen conditions and ordering | [Continuation manifest](protocol/ollama-fra6s004-cpu-continuation/manifest.json) | Exact parent plan, 28 selected plans, 12 traces, 19 original-prefix files and 14 collection methods |
| Lossless raw evidence | [Per-file gzip index](archives/cpu-native-baseline/evidence-index.json) | Compressed and decoded SHA-256 and byte lengths for all 19 prefix files and 185 continuation files |
| Original export inventory | [Collection inventory](archives/v2-ollama-fra6s004-cpu-continuation/collection-manifest.json) | Original decoded identities; replay does not require a monolithic transfer archive |
| Load-selection pilot | [Pilot archive](archives/v2-ollama-fra6s004-cpu-confirmation-pilot/manifest.json) | All 52 classifications retained separately from the comparison |
| Executed instrument bytes | [Source registry](instruments/manifest.json) | Exact BBA source identity, model-controller/proxy hashes and the separately recorded Git base |
| Independent offline verification | [Native checker](scripts/check_cpu_continuation.py) | 45 observation replays, 44 native certificates with 1,490 complete bodies, 29 reset proofs, 112 technical warmups and exact report/CSV comparison |

The source identity is
`sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f`.
The manifest binds the executed model, options, resource envelope, traces and
methods. The 1,490 bodies are an evidence inventory across certified trials,
not independent replications or a throughput estimate.

The primary outcome follows every measurement arrival through its ten-second
client deadline over a 335-second cohort after a five-second arrival warmup.
A late native HTTP 200 with `done=true` never substitutes for a failed client
outcome. The task classifies a controlled HTTP status into a short JSON label;
it does not establish general model or retrieval quality.

The prefix shares one inference epoch. Each suffix condition starts with a
proved native-process restart and four technical warmup responses. The suffix
also has three refreshed transport controls and all twelve qualification trials.
These lifecycle regimes remain separately labelled. No means, confidence
intervals, significance tests or causal replica comparison pool them. The
uncertain observation was not rerun, replaced or relabelled.

Paths in a report's `source_path` identify decoded collection files, not download
URLs. The gzip index maps each such path to its public compressed bytes. Header
mtime is zero; original JSON timestamps and response bodies are unchanged.

## Paired shared native-admission experiment

The separate 24-arm experiment has twelve complete pairs and complete native
certificates for every arm. Its 116 original files are retained as
[individually hashed gzip files](archives/shared-admission/evidence-index.json).
The [JSON report](analysis/v2-ollama-shared-admission/results.json) and
[trial CSV](analysis/v2-ollama-shared-admission/trials.csv) expose every client
cohort, native certificate, refusal count and paired difference.
The [paired-outcome figure](analysis/v2-ollama-shared-admission/shared-admission-pairs.svg)
shows all twelve pairs without pooling arrivals; regenerate it with
`python3 scripts/plot_shared_admission.py` and the optional analysis dependencies.

The [frozen manifest](protocol/shared-admission/manifest.json) binds the extended
instrument to the unchanged BBA generation path, all twenty-four selected plans,
the retained calibration and randomized arm order. Offline replay checks the
three network controls, twelve generator controls, twenty-five reset proofs,
ninety-six technical warmup responses, and 844 complete native trial responses.
The 844 admitted calls and 292 shared-cap refusals count the full trace, including
arrival warmup; they are not the primary measurement-arrival denominator.

The [native-admission checker](scripts/check_shared_admission.py) restores the
exact decoded files, verifies the export inventory, and recalculates the JSON
and CSV exactly without an inference server. Transfer capsules remain local;
each public gzip is below 8 MB. Original collection timestamps are preserved.
These twelve contrasts remain separate from the older native baseline and the
emulator datasets. Their results concern one fixed policy, not a production
library or universal scheduling benefit.

## Qualified emulator evidence

| Collection | Evidence |
| --- | --- |
| Primary 72-trial matrix | `archives/v2-linux-primary`, `analysis/v2-linux-primary` |
| Twelve provider-only controls | `archives/v2-linux-provider-control`, `analysis/v2-linux-provider-control` |
| Twelve quota interventions | `archives/v2-linux-quota-sensitivity`, `analysis/v2-linux-quota-sensitivity` |
| Independent 4/8 CPU qualifications and 48 paired contrasts | `archives/v2-independent-*`, `analysis/v2-independent-*` |
| Source-linked figures and condition tables | [Combined report](analysis/v2-study/results.json), [CSV](analysis/v2-study/trials.csv), [tables](analysis/v2-study/tables.md) |

Each collection retains its complete qualification, image/source identity,
serving pod UIDs, traces, client failures, quota ledger and resource samples.
The independent Linux run is linked by
[evidence/v2-linux-execution.json](evidence/v2-linux-execution.json).
Different hosts and source identities are not pooled as hardware replications.
Emulator quota units remain declared consumption, not actual LLM tokens.

The generator runs inside Kubernetes and calls the workers' ClusterIP Service.
Only its controller is forwarded to the operator. A worker-Service port forward
would select one pod and would not establish distribution across replicas.

## Excluded collections remain inspectable

The original 21-trial pilot, local calibration blocks, historical local
actual-model block, failed remote transport controls and interrupted host/client
attempts remain excluded under their own archive identities. A passing subset
never repairs a failed block or substitutes for an unobserved condition.

- [Original method audit](AUDIT.md) and `archives/legacy-methods/` retain the
  original exclusion criteria.
- `archives/2026-11-12-calibration-*` and `archives/2026-11-12-focused-linux-v3`
  retain earlier diagnostic and independently hosted observations.
- `archives/v2-local-*` retain the local model and calibration exclusions;
  `archives/v2-model-pilot` retains that separate closed-loop pilot.
- `archives/v2-remote-*`, `archives/v2-ollama-isolated-*` and the other named
  host/client diagnostic archives retain failed transport or collection gates.
- The native baseline's per-file index includes the complete failed prefix:
  its block status, collection manifest, sixteen certified observations and the
  unchanged uncertain observation.

These records preserve alternative explanations; they do not uniquely diagnose
a Mac, VM, GPU, network, memory or scheduler cause. A later successful apparatus
does not change an earlier failure's eligibility.

The Dell memory exclusion and FRA planning/inspection snapshots use standalone
`evidence-index.json` inventories. Their 120 lossless files include incomplete
operational source contexts. The native checker verifies their exact bytes
separately and does not treat readiness snapshots as qualified trial manifests.

## Recalculate from a fresh checkout

Python 3.12's standard library is sufficient. No Ollama service, model weights,
cluster or customer data is required. Loopback integration tests require local
sockets.

```sh
python3 scripts/check_cpu_continuation.py --restore-only
python3 scripts/check_shared_admission.py --restore-only
python3 -m unittest discover -p 'test_*.py'
python3 scripts/verify_evidence.py
python3 -m v2.verify
python3 scripts/check_cpu_continuation.py
python3 scripts/check_shared_admission.py --mode final
python3 -m v2.report --check
python3 -m v2.publication --check
```

The native checker restores all 204 indexed files and the separately archived
calibration pilot at their exact ignored paths. It refuses different existing
bytes. It then invokes the frozen scientific methods, replays the complete
observations and compares every scientific JSON field and the full CSV exactly.
Both collected and replayed Python patch versions are recorded; only that
provenance field may differ between Python 3.12 patch releases.

The historical checker retains its own [complete archive inventory](v2/evidence-counts.json).
That inventory is generated by [scanning the historical collection indexes](scripts/inventory_legacy_evidence.py).
CI executes both checks and preserves their outputs. It performs no new load
experiment and establishes no production or proposed-library benefit.
