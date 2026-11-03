#!/usr/bin/env bash
# Run only inside the new research VM, never on the shared hypervisor.
set -euo pipefail
cloud-init status --wait >/dev/null
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io git curl python3-venv openssh-server lsof
systemctl enable --now docker
curl --fail --location --silent --show-error \
  https://github.com/kubernetes-sigs/kind/releases/download/v0.32.0/kind-linux-amd64 \
  -o /usr/local/bin/kind
printf '%s  %s\n' '50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54' /usr/local/bin/kind | sha256sum --check
chmod +x /usr/local/bin/kind
curl --fail --location --silent --show-error https://dl.k8s.io/release/v1.36.1/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl
curl --fail --location --silent --show-error https://dl.k8s.io/release/v1.36.1/bin/linux/amd64/kubectl.sha256 -o /tmp/kubectl.sha256
printf '%s  %s\n' "$(cat /tmp/kubectl.sha256)" /usr/local/bin/kubectl | sha256sum --check
chmod +x /usr/local/bin/kubectl
git clone --branch research/capacity-validation-v2 --single-branch \
  https://github.com/Alien6-Studio/research-llm-kubernetes.git /opt/alien6-capacity-v2
cd /opt/alien6-capacity-v2
kind create cluster --config v2/kind.yaml --kubeconfig .kubeconfig-capacity-v2 --wait 120s
docker update --cpus 4 --memory 8g --memory-swap 8g a6-capacity-v2-control-plane
IMAGE="alien6-capacity:v2-$(git rev-parse --short=12 HEAD)"
docker build -f v2/Dockerfile --build-arg SOURCE_COMMIT="$(git rev-parse HEAD)" -t "$IMAGE" .
kind load docker-image "$IMAGE" --name a6-capacity-v2
python3 -m v2.run deploy --image "$IMAGE"
