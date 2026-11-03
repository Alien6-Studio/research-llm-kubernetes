#!/usr/bin/env bash
# Install only inside the newly provisioned private Ubuntu research VM.
set -euo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help && $# -eq 1 ]]; then
    printf '%s\n' \
        'Usage: bash infra/bootstrap-fra6s004-client.sh --source-id sha256:HEX64 --git-base HEX40_OR_HEX64' \
        'Requires root in the new a6-capacity-v2 Ubuntu 24.04 x86_64 VM.' \
        'Marker /etc/alien6-research-instance must contain a6-capacity-v2.' \
        'Private IPv4 must be 172.30.64.2 with gateway 172.30.64.1.' \
        'Materialize the exact registered source in /opt/alien6-fra6s004-cpu first.' \
        'Both identities are mandatory; no host commands are run.'
    exit 0
fi
source_id=
git_base=
while [[ $# -gt 0 ]]; do
    case $1 in
        --source-id)
            [[ $# -ge 2 && -z $source_id ]] || fail 'Provide --source-id exactly once with its value.'
            source_id=$2
            shift 2
            ;;
        --git-base)
            [[ $# -ge 2 && -z $git_base ]] || fail 'Provide --git-base exactly once with its value.'
            git_base=$2
            shift 2
            ;;
        *) fail 'Unexpected argument; use --help.' ;;
    esac
done
[[ -n $source_id && -n $git_base ]] || fail 'Both --source-id and --git-base are required.'
[[ $source_id =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'Expected --source-id in sha256:HEX64 format.'
[[ $git_base =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || fail 'Expected --git-base as a full 40- or 64-character lowercase hexadecimal identity.'

# Every instance guard precedes cloud-init, package installation and downloads.
[[ ${EUID} -eq 0 ]] || fail 'Run as root only inside the dedicated research VM.'
[[ -f /etc/alien6-research-instance && ! -L /etc/alien6-research-instance ]] || fail 'Missing dedicated-instance marker.'
[[ $(stat -c %u /etc/alien6-research-instance) == 0 ]] || fail 'The dedicated-instance marker must be root-owned.'
[[ $(</etc/alien6-research-instance) == a6-capacity-v2 ]] || fail 'Unexpected dedicated-instance marker.'
[[ $(hostname -s) == a6-capacity-v2 ]] || fail 'Unexpected VM hostname.'
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Expected Linux x86_64.'
virtualization=$(systemd-detect-virt --vm) || fail 'Expected an isolated virtual machine, not a host or container.'
[[ $virtualization == kvm || $virtualization == qemu ]] || fail 'Expected the dedicated QEMU/KVM VM.'
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || fail 'Expected Ubuntu 24.04.'
command -v ip >/dev/null || fail 'The VM image must include iproute2.'
ip -o -4 address show | awk '$4 ~ /^172\.30\.64\.2\// {found=1} END {exit !found}' || fail 'Unexpected VM private IPv4 address.'
[[ $(ip -4 route show default | wc -l) -eq 1 ]] || fail 'Expected one private default route.'
ip -4 route show default | awk '$2=="via" && $3=="172.30.64.1" {found=1} END {exit !found}' || fail 'Unexpected private default gateway.'
command -v python3 >/dev/null || fail 'The VM image must include Python 3.'
python3 - <<'PY'
import os
from pathlib import Path
if len(os.sched_getaffinity(0)) != 8:
    raise SystemExit('Expected the new eight-vCPU client VM')
memory = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
if not 14 * 1024**3 <= memory <= 17 * 1024**3:
    raise SystemExit('Expected the new 16 GiB client VM')
PY

source_root=/opt/alien6-fra6s004-cpu
script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
artifacts="$script_dir/fra6s004-client-artifacts.json"
[[ -d $source_root && -f $artifacts ]] || fail 'Stage the exact source and companion artifact manifest first.'
[[ ! -e $source_root/.kubeconfig-capacity-v2 ]] || fail 'A study kubeconfig already exists; do not overwrite a cluster.'
[[ ! -e /var/run/docker.sock ]] || fail 'A Docker socket already exists; inspect this VM before provisioning.'
if command -v docker >/dev/null || command -v containerd >/dev/null; then
    fail 'Container tooling already exists; this bootstrap requires a fresh VM.'
fi
python3 - "$source_root" "$source_id" <<'SOURCE_CHECK'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
source=sys.argv[2]
sources=json.loads((root/'instruments/manifest.json').read_text())['sources']
if source not in sources:
    raise SystemExit('The requested source identity is not registered; materialize its exact registered bytes first')
entry=sources[source]
if not entry['files']:
    raise SystemExit('The requested source registration contains no files')
descriptor={'format':entry['content_format'],'files':{name:{'bytes':item['bytes'],'sha256':item['sha256']} for name,item in entry['files'].items()}}
registered='sha256:'+hashlib.sha256(json.dumps(descriptor,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if registered!=source:
    raise SystemExit('The requested source registration does not match its content identity')
for name, item in entry['files'].items():
    path=(root/name).resolve()
    if not path.is_relative_to(root) or path.stat().st_size!=item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        raise SystemExit('The staged measurement source differs from the frozen instrument')
if hashlib.sha256((root/'v2/kind.yaml').read_bytes()).hexdigest()!='fe23ecd2a152e577f6991d68a5f1dfc7d00175f685efa2cad69c428b397c800c':
    raise SystemExit('The kind configuration differs from the pinned one-node study')
SOURCE_CHECK

# Installation starts only after all instance and source guards pass.
cloud-init status --wait >/dev/null
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends -qq \
    ca-certificates curl git python3-venv lsof
cache=/var/cache/alien6-research/client-bootstrap
install -d -m 0700 "$cache"

verify_file() {
    local file=$1 sha=$2 bytes=$3
    [[ $bytes == - || $(stat -c %s "$file") == "$bytes" ]] || fail "Artifact byte length differs: $(basename "$file")"
    printf '%s  %s\n' "$sha" "$file" | sha256sum --check --status || fail "Artifact SHA-256 differs: $(basename "$file")"
}
mapfile -t records < <(python3 - "$artifacts" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
if data['format']!='alien6-client-bootstrap-artifacts-v1':raise SystemExit('Unexpected artifact manifest')
for item in data['artifacts']:
    print('\t'.join(str(item.get(key,'-')) for key in ('kind','name','version','filename','sha256','bytes','url')))
PY
)
[[ ${#records[@]} -eq 6 ]] || fail 'Expected all six pinned client artifacts.'
debs=()
for record in "${records[@]}"; do
    IFS=$'\t' read -r type name version filename sha bytes url <<<"$record"
    [[ $filename != */* && $sha =~ ^[0-9a-f]{64}$ && $url == https://* ]] || fail 'Unsafe pinned artifact entry.'
    target="$cache/$filename"
    if [[ ! -f $target ]]; then
        if [[ ! -f $target.partial ]] || ! printf '%s  %s\n' "$sha" "$target.partial" | sha256sum --check --status 2>/dev/null; then
            curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
                --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 30 \
                --continue-at - --output "$target.partial" "$url"
        fi
        verify_file "$target.partial" "$sha" "$bytes"
        mv -- "$target.partial" "$target"
    fi
    verify_file "$target" "$sha" "$bytes"
    if [[ $type == deb ]]; then
        [[ $(dpkg-deb --field "$target" Package) == "$name" && $(dpkg-deb --field "$target" Version) == "$version" ]] || fail 'Pinned package metadata differs.'
        debs+=("$target")
    elif [[ $type == binary && ( $name == kind || $name == kubectl ) ]]; then
        install -m 0755 "$target" "/usr/local/bin/$name"
    else
        fail 'Unexpected pinned artifact type.'
    fi
done
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends -qq "${debs[@]}"
apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin >/dev/null
systemctl enable --now docker.service
[[ $(docker version --format '{{.Server.Version}}') == 28.2.2 ]] || fail 'Unexpected Docker server version.'
[[ $(docker version --format '{{.Client.Version}}') == 28.2.2 ]] || fail 'Unexpected Docker client version.'
[[ $(kind version) == 'kind v0.32.0 '* ]] || fail 'Unexpected kind version.'
kubectl version --client --output=json | python3 -c 'import json,sys; assert json.load(sys.stdin)["clientVersion"]["gitVersion"]=="v1.36.1"'

cd "$source_root"
# Establish only the public base reference; never check out older source over the
# separately verified measurement files staged by the operator.
if [[ ! -d .git ]]; then
    git init --initial-branch=main >/dev/null
    git remote add origin https://github.com/Alien6-Studio/research-llm-kubernetes.git
    git -c protocol.version=2 fetch --quiet --filter=blob:none --no-tags --depth=1 \
        origin "$git_base"
    git update-ref refs/heads/main "$git_base"
fi
PYTHONDONTWRITEBYTECODE=1 python3 - "$source_id" "$git_base" <<'GIT_SOURCE_CHECK'
import sys
from pathlib import Path
from v2.identity import capture,verify_worktree
snapshot=capture(Path.cwd());verify_worktree(Path.cwd(),snapshot.source_id)
if snapshot.source_id!=sys.argv[1] or snapshot.git_base!=sys.argv[2]:
    raise SystemExit('Unexpected source identity or Git base')
GIT_SOURCE_CHECK
kubeconfig="$source_root/.kubeconfig-capacity-v2"
kind create cluster --config "$source_root/v2/kind.yaml" --name a6-capacity-v2 \
    --kubeconfig "$kubeconfig" --wait 180s
chmod 0600 "$kubeconfig"
docker update --cpus 4 --memory 8g --memory-swap 8g a6-capacity-v2-control-plane >/dev/null
kubectl --kubeconfig "$kubeconfig" wait --for=condition=Ready node --all --timeout=120s
kubectl --kubeconfig "$kubeconfig" create namespace a6-capacity-v2
docker inspect --format '{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
    a6-capacity-v2-control-plane | grep -qx '4000000000 8589934592 8589934592' || fail 'The study node resource envelope differs.'
[[ $(stat -c %a "$kubeconfig") == 600 ]] || fail 'The study kubeconfig must remain mode 0600.'
printf '%s\n' 'Client bootstrap complete: one pinned study cluster, node CPU4/memory8GiB, private kubeconfig0600. No model or experiment was started.'
