"""Identify and preserve exact collection source independently of Git history."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


FORMAT = 'alien6-collection-source-v1'
PREFIX = 'sha256:'
# Collection code and its orchestration/transport drivers. Plans, workloads,
# images and runtime settings retain their own identities in each run manifest.
MEASURED_FILES = (
    'v2/Dockerfile', 'v2/Model.Dockerfile', 'v2/__init__.py',
    'v2/identity.py', 'v2/inference_monitor.py', 'v2/inference_proxy.py',
    'v2/model.py', 'v2/model_service.py', 'v2/network_policy.py',
    'v2/network_probe.py', 'v2/plan.py', 'v2/quota.py', 'v2/remote_plan.py',
    'v2/run.py', 'v2/service.py', 'v2/transport.py', 'v2/tunnel_bridge.py',
    'v2/workload.py',
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative_name(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Expected a canonical relative source path')
    relative = PurePosixPath(name)
    if relative.is_absolute() or relative.as_posix() != name or any(
        part in ('.', '..') for part in relative.parts
    ):
        raise ValueError('Unsafe or noncanonical source path')
    return relative


def _safe_path(root, name):
    relative = _relative_name(name)
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError('Source path escapes the repository')
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Source paths must not contain symbolic links')
    return path


def _descriptor(files):
    result = {}
    for name, raw in files:
        if name in result or not isinstance(raw, bytes):
            raise ValueError('Expected unique source paths and exact byte contents')
        # Validate even snapshots constructed by a caller rather than capture().
        _relative_name(name)
        result[name] = {'sha256': _sha256(raw), 'bytes': len(raw)}
    if not result:
        raise ValueError('A source identity cannot describe an empty instrument')
    return {'format': FORMAT, 'files': dict(sorted(result.items()))}


def _content_id(files):
    return PREFIX + _sha256(_canonical(_descriptor(files)))


def _blob_name(digest):
    return f'instruments/blobs/{digest}.snapshot'


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    git_base: str
    files: tuple

    def metadata(self):
        """Git base is context, not a claim that Git contains these bytes."""
        return {'source_id': self.source_id, 'git_base': self.git_base}


def capture(root, files=MEASURED_FILES, *, git_base=None):
    """Read source bytes once; changes after capture cannot alter this snapshot."""
    root = Path(root).resolve()
    names = tuple(files)
    if len(names) != len(set(names)):
        raise ValueError('Duplicate source paths')
    captured = tuple((name, _safe_path(root, name).read_bytes()) for name in sorted(names))
    if git_base is None:
        git_base = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    if not isinstance(git_base, str) or len(git_base) not in (40, 64) or any(
        char not in '0123456789abcdef' for char in git_base
    ):
        raise ValueError('Record the actual full Git base object ID separately')
    return SourceSnapshot(_content_id(captured), git_base, captured)


def _entry(snapshot):
    descriptor = _descriptor(snapshot.files)
    if snapshot.source_id != PREFIX + _sha256(_canonical(descriptor)):
        raise ValueError('Snapshot content identity does not match its bytes')
    return {
        'content_format': FORMAT,
        'files': {
            name: {**record, 'path': _blob_name(record['sha256'])}
            for name, record in descriptor['files'].items()
        },
    }


@contextmanager
def _registry_lock(root):
    lock = _safe_path(root, 'instruments/.identity.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def register(root, snapshot):
    """Append compatible content-addressed snapshots; never replace stored bytes."""
    root = Path(root).resolve()
    entry = _entry(snapshot)
    manifest = _safe_path(root, 'instruments/manifest.json')
    with _registry_lock(root):
        data = json.loads(manifest.read_text()) if manifest.exists() else {
            'scope': 'Exact measured source bytes, indexed by recorded source identifiers and SHA-256.',
            'sources': {},
        }
        sources = data['sources']
        if not isinstance(sources, dict):
            raise ValueError('Invalid source registry')
        existing = sources.get(snapshot.source_id)
        if existing is not None and existing != entry:
            raise ValueError('Refusing to replace a different source registry entry')
        # Validate every existing blob before performing any write.
        for name, raw in snapshot.files:
            blob = _safe_path(root, entry['files'][name]['path'])
            if blob.exists() and blob.read_bytes() != raw:
                raise ValueError('Refusing to overwrite a different source blob')
        for name, raw in snapshot.files:
            blob = _safe_path(root, entry['files'][name]['path'])
            blob.parent.mkdir(parents=True, exist_ok=True)
            if not blob.exists():
                with blob.open('xb') as destination:
                    destination.write(raw)
        if existing is not None:
            return snapshot.metadata()
        sources[snapshot.source_id] = entry
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=manifest.parent, prefix='.identity-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, manifest.stat().st_mode & 0o777 if manifest.exists() else 0o644)
            os.replace(temporary, manifest)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return snapshot.metadata()


def verify_registered(root, source_id):
    """Verify the exact archived bytes and recompute a content-based source ID."""
    root = Path(root).resolve()
    manifest = json.loads(_safe_path(root, 'instruments/manifest.json').read_text())
    entry = manifest['sources'][source_id]
    if entry.get('content_format') != FORMAT:
        raise ValueError('Expected a content-addressed collection source')
    captured = []
    for name, record in sorted(entry['files'].items()):
        _safe_path(root, name)
        if record['path'] != _blob_name(record['sha256']):
            raise ValueError('Unexpected source blob path')
        raw = _safe_path(root, record['path']).read_bytes()
        if len(raw) != record['bytes'] or _sha256(raw) != record['sha256']:
            raise ValueError('Registered source bytes differ from their identity')
        captured.append((name, raw))
    if _content_id(captured) != source_id:
        raise ValueError('Source entry differs from its content identity')
    return entry


def verify_worktree(root, source_id):
    """Fail if the source to be executed has changed since registration."""
    root = Path(root).resolve()
    entry = verify_registered(root, source_id)
    for name, record in entry['files'].items():
        raw = _safe_path(root, name).read_bytes()
        if len(raw) != record['bytes'] or _sha256(raw) != record['sha256']:
            raise ValueError(f'Working source changed after registration: {name}')
    return entry
