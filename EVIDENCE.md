# Evidence and provenance

This repository distinguishes implemented instrumentation from collected
measurements. **The first 21 historical load trials remain excluded. V2 adds a complete 72-trial emulator matrix, quota controls and 48 paired CPU trials. The actual-model comparison remains unqualified.**

[V2 results and limitations](RESULTS_V2.md) · [Original-method audit](AUDIT.md).

## Historical source and evidence

The following original instrumentation and archives describe the 12 November campaign. V2 sources and observations are indexed separately below.

| Evidence | Location |
| --- | --- |
| Research question and controls | [PROTOCOL.md](PROTOCOL.md) |
| Isolated cluster definition | [kind configuration](infra/kind.yaml), Kubernetes 1.36.1 |
| Pinned base image | [Dockerfile](Dockerfile), official Python image digest |
| Executable HTTP services | [service.py](service.py) |
| Arrival traces and orchestration | [run.py](run.py) |
| Quota and load controls | [test_study.py](test_study.py), [test execution](evidence/unit-tests.json) |

Trials retain planned arrivals, observed responses, failures, deadlines, and
resource samples. Manifests record source commits and the executable checksum
actually reported by the pods.

The load generator runs inside Kubernetes and calls the workers' ClusterIP
Service. Port-forwarding the workers' Service selects one pod and would not
establish traffic distribution across replicas. Only the load controller is
forwarded to the local host.

Emulator trials use declared quota units. The original real-model phase was pending at that checkpoint. New actual generations and their exclusions are indexed below; emulator units and real token counts remain separate.

The [validity protocol](VALIDITY.md) records the VM/host confounds and publication
gates. The complete pilot block is preserved, including initially passing trials.


The complete v 2 calibration block and the failed control-channel attempt are
preserved as lossless gzip JSON/text copies in
`archives/2026-11-12-calibration-v2/`. Its manifest identifies both archive and
decoded SHA-256 values; no response was edited. The report in
`evidence/calibration-v2-report.json` retains all twelve controls and explains
why successful HTTP replies alone did not establish generator headroom.

Version 3 and its fixed request traces are a prospective diagnostic correction,
not confirmation of the original capacity claim. The independent Linux workflow
retains failed qualification and never proceeds to capacity contrasts unless the
complete calibration block passes the unchanged gates.


## Version 3 local and independently hosted collections

`archives/2026-11-12-calibration-v3/` and
`archives/2026-11-12-calibration-linux-v3/` retain both complete twelve-trial
blocks. `archives/2026-11-12-focused-linux-v3/` retains every Linux essential
contrast. Each manifest identifies compressed and decoded bytes. The original
collection manifests preserve source, service, trace, hardware and image identity.
`evidence/independent-linux-run.json` links the hosted execution and its source SHA.

`analysis/2026-11-12-calibration/` and `analysis/2026-11-12-focused-linux/` contain
recomputed trial summaries and resource diagnostics. CI audits these calculations
without rerunning the services. Sampled resources and finite heartbeat retention
are explicit limits; missing continuous restart counters do not establish absence
of restarts. No failure is replaced or removed from a denominator.


## V2 collections and source identities

| Collection | Executed source | Archive / analysis |
| --- | --- | --- |
| Linux qualification, provider controls,72-arm matrix and quota sensitivity |`b6d035ae6b53fdd67de08a4b3e6f1501ccda7016` |`archives/v2-linux-*`, `analysis/v2-linux-*` |
| Native model pilot |`6f9dc2a` |`archives/v2-model-pilot` |
| Local model-path qualification and excluded 45-trial actual block |`8a89116350dee1c090f011eb6e710f23abfde608` |`archives/v2-local-model-qualification`, `archives/v2-local-real-model` |
| Independent 4/8 CPU qualifications and 48 paired trials |`1f589b90893cf5728935420d02942451f0ea6b61` |`archives/v2-independent-*`, `analysis/v2-independent-*` |
| Original remote transport control |`aee2a4bf10ccac69cb16e066e196b685dcfd1eab` |`archives/v2-remote-network` |
| Compressed transport control |`38889ce` |`archives/v2-remote-compressed-network` |
| Response-size fixture control |`1f1b365` |`archives/v2-remote-fixture-network` |

The local 4/8 CPU qualification blocks and the interrupted model-control attempt
are also retained under `archives/v2-local-calibration-*` and
`archives/v2-model-control-interruption`. Later changes never replace earlier
observations or make their passing subsets eligible for inference.

Each archive manifest records compressed and decoded SHA-256 and byte lengths.
Trial/block manifests retain full source and image IDs, pod identities, traces,
individual failures and resource records. Sources available in Git are the
preregistration checkpoints; source-file checksums reported by running roles
are verified offline against the corresponding manifest, not the latest code.

The independent Linux collection is publicly traceable to
[workflow43395996529](https://github.com/Alien6-Studio/research-llm-kubernetes/actions/runs/43395996529).
[evidence/v2-linux-execution.json](evidence/v2-linux-execution.json) distinguishes
runtime observations from the runner inventory's inferred Docker version.
[evidence/v2-vm-identity.json](evidence/v2-vm-identity.json) records the selected
image fingerprint and actual VM limits. The native server settings are in
[evidence/v2-model-server-settings.json](evidence/v2-model-server-settings.json).
Transport relay counters are retained in `evidence/v2-transport-bridge.jsonl.gz`.

`python -m v2.verify` replays 261 complete trial records, the native classifications
and all three failed remote transport controls, and compares committed numerical
analyses. The original checksum inventories and deterministic analyses remain
separately executable. [Generated figures, tables and contrasts](analysis/v2-study/)
come from `v2/analyze.py` and `v2/report.py`. Reproducibility of a calculation is
not qualification of an invalid collection.
