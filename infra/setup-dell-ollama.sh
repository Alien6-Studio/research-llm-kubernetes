#!/usr/bin/env bash
# Run inside the new inference container only. No model weights are downloaded.
# The operator must first create /etc/alien6-research-instance with one line:
# a6-ollama-study
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
site=dell
if [[ $# -gt 0 ]]; then
    [[ $# -eq 2 && $1 == --site ]] || fail 'Usage: setup-dell-ollama.sh [--site dell|fra6s004]'
    site=$2
fi
case "$site" in
    dell) inference_ip=172.16.0.244; firewall=dell-cpu-instance.nft ;;
    fra6s004) inference_ip=172.30.64.3; firewall=fra6s004-cpu-instance.nft ;;
    *) fail 'Unknown study site; no configuration was applied.' ;;
esac
[[ ${EUID} -eq 0 ]] || fail 'Run as root inside the dedicated study container.'
[[ -r /run/systemd/container && $(</run/systemd/container) == lxc ]] || fail 'Expected an LXC container.'
[[ $(hostname -s) == a6-ollama-study ]] || fail 'Unexpected instance hostname.'
[[ -r /etc/alien6-research-instance && $(</etc/alien6-research-instance) == a6-ollama-study ]] || fail 'Missing dedicated-instance marker.'
command -v ip >/dev/null || fail 'The base image must include iproute2.'
ip -o -4 address show | awk -v expected="$inference_ip" '{split($4,address,"/"); if(address[1]==expected) found=1} END {exit !found}' || fail 'Expected the dedicated private IPv4 address for this site.'
[[ $(uname -m) == x86_64 ]] || fail 'The pinned binary requires Linux x86_64.'

units=(alien6-ollama.service alien6-ollama-proxy.service alien6-ollama-monitor.service)
for unit in "${units[@]}"; do
    if systemctl is-active --quiet "$unit"; then
        fail "Stop the study services before provisioning: $unit"
    fi
done
if ss -H -ltn | awk '$4 ~ /:(11435|11436|11437)$/ {found=1} END {exit !found}'; then
    fail 'A study port is already listening; do not provision during a campaign.'
fi
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for artifact in "$firewall" "${units[@]}"; do
    [[ -r "$script_dir/$artifact" ]] || fail "Missing companion file: $artifact"
done

# Disable only this new container's optional SSH listener; use lxc exec for access.
for unit in ssh.service ssh.socket; do
    if systemctl list-unit-files --no-legend "$unit" | grep -q "^$unit[[:space:]]"; then
        systemctl disable --now "$unit"
    fi
done

# HTTPS sources allow the same provisioning command to work after egress is filtered.
if [[ ! -s /etc/ssl/certs/ca-certificates.crt ]]; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates
fi
for source in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
    if [[ -f "$source" ]]; then
        sed -i -E 's#http://([a-zA-Z0-9.-]*ubuntu\.com)/#https://\1/#g' "$source"
    fi
done
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 git lsof procps curl ca-certificates zstd nftables

# This flush applies to the guarded container's network namespace only.
install -m 0644 "$script_dir/$firewall" /etc/nftables.conf
nft --check --file /etc/nftables.conf
nft --file /etc/nftables.conf
systemctl enable --now nftables.service

state_dir=/var/lib/alien6-ollama
source_dir=/opt/alien6-research
if ! id -u alien6-study >/dev/null 2>&1; then
    useradd --system --user-group --create-home --home-dir "$state_dir" \
        --shell /usr/sbin/nologin alien6-study
fi
install -d -m 0755 -o alien6-study -g alien6-study "$state_dir" "$state_dir/models"
install -d -m 0755 "$source_dir" /etc/alien6-research /var/cache/alien6-research /opt/ollama
printf 'STUDY_PROXY_BIND=%s\nSTUDY_PROXY_PORT=11437\nSTUDY_MONITOR_BIND=%s\nSTUDY_OLLAMA_URL=http://127.0.0.1:11435\n' \
    "$inference_ip" "$inference_ip" > /etc/alien6-research/endpoints.env
chmod 0644 /etc/alien6-research/endpoints.env
printf '%s\n' "$site" > /etc/alien6-research/site
# The verified checkout remains root-owned and read-only to the service user.
git config --file "$state_dir/.gitconfig" --replace-all safe.directory "$source_dir"
chown alien6-study:alien6-study "$state_dir/.gitconfig"
chmod 0644 "$state_dir/.gitconfig"

version=0.40.16
expected_bytes=1545767640
expected_sha256=87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d
url="https://github.com/ollama/ollama/releases/download/v${version}/ollama-linux-amd64.tar.zst"
archive="/var/cache/alien6-research/ollama-linux-amd64-v${version}.tar.zst"
partial="${archive}.part"
verify_archive() {
    [[ $(stat -c %s "$1") == "$expected_bytes" ]] || fail 'Ollama archive size differs from the pinned artifact.'
    printf '%s  %s\n' "$expected_sha256" "$1" | sha256sum --check --status || fail 'Ollama archive SHA-256 mismatch; preserve and inspect the download.'
}
if [[ ! -f "$archive" ]]; then
    if [[ -f "$partial" && $(stat -c %s "$partial") -gt "$expected_bytes" ]]; then
        fail 'Partial archive is larger than the pinned artifact.'
    fi
    if [[ ! -f "$partial" || $(stat -c %s "$partial") -lt "$expected_bytes" ]]; then
        curl --fail --location --show-error --proto '=https' --proto-redir '=https' \
            --connect-timeout 20 --retry 8 --retry-delay 3 --retry-all-errors \
            --continue-at - --output "$partial" "$url"
    fi
    verify_archive "$partial"
    mv -- "$partial" "$archive"
fi
verify_archive "$archive"
tar --zstd --extract --file "$archive" --directory /opt/ollama --no-same-owner
[[ -x /opt/ollama/bin/ollama ]] || fail 'The verified archive did not provide bin/ollama.'
printf '{"version":"%s","url":"%s","sha256":"%s","bytes":%s}\n' \
    "$version" "$url" "$expected_sha256" "$expected_bytes" \
    > /etc/alien6-research/ollama-artifact.json

for unit in "${units[@]}"; do
    install -m 0644 "$script_dir/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl disable "${units[@]}"
printf '%s\n' \
    'Pinned Ollama installed; no weights downloaded and no study service started.' \
    'Deploy the verified checkout to /opt/alien6-research and verify the model artifacts.' \
    'Record scheduler settings in /etc/alien6-research/ollama.env.' \
    'Create /etc/alien6-research/ready-to-serve only after operator verification, then start services manually.'
