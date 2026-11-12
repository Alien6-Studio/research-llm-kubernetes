# Evidence and provenance

This repository distinguishes implemented instrumentation from collected
measurements. **The first 21 load trials are a retained, excluded pilot; the full confirmatory matrix remains pending. The three complete qualification blocks and twelve independently hosted emulator contrasts are now audited below.**

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

Emulator trials use declared quota units. The planned Ollama phase will retain actual model
input/output token counts; it has not been collected. Emulator and real-model
measurements must remain separate.

The [validity protocol](VALIDITY.md) records the VM/host confounds and publication
gates. The complete pilot block is preserved, including initially passing trials.


The complete v2 calibration block and the failed control-channel attempt are
preserved as lossless gzip JSON/text copies in
`archives/2026-11-12-calibration-v2/`. Its manifest identifies both archive and
decoded SHA-256 values; no response was edited. The report in
`evidence/calibration-v2-report.json` retains all twelve controls and explains
why successful HTTP replies alone did not establish generator headroom.

Version3 and its fixed request traces are a prospective diagnostic correction,
not confirmation of the original capacity claim. The independent Linux workflow
retains failed qualification and never proceeds to capacity contrasts unless the
complete calibration block passes the unchanged gates.


## Version3 local and independently hosted collections

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
