# Evidence and provenance

This repository distinguishes implemented instrumentation from collected
measurements. **The load campaign has not yet been executed.**

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

Emulator trials use declared quota units. Ollama trials retain actual model
input/output token counts. These two campaigns are analysed separately.
