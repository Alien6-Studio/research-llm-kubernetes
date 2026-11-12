# Third-party material

Original code and project documentation are licensed under Apache-2.0.
Docker base images, kind node images, Kubernetes, Ollama, model weights, and
other tools retain their upstream licenses. They are not relicensed here.

- `Dockerfile` pins the official Python base image by digest.
- `infra/kind.yaml` pins the kind node image by digest.
- `PROTOCOL.md` and run manifests identify the Ollama model and full digest.
- Model weights and container layers are not bundled in this repository.

Run records contain observations produced by the study. A repository license
badge does not grant additional rights to third-party tools or model weights.
