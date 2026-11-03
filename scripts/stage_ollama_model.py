"""Stage the study's exact Ollama model from official, content-addressed blobs.

This is a deployment/reproduction helper, not an experiment. It never pulls a
mutable model tag, bundles weights in Git, or starts an Ollama server. Downloaded
blobs must pass both byte-length and SHA-256 checks before the byte-identical
named manifest is installed. Run against a dedicated model directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'qwen3.6:latest'
MANIFEST_SHA256 = '07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522'
MANIFEST_FILE = ROOT / 'protocol/model-artifacts/qwen3.6-07d35212591f.manifest.json'
REGISTRY_BLOBS = 'https://registry.ollama.ai/v2/library/qwen3.6/blobs/'
MANIFEST_RELATIVE = Path('manifests/registry.ollama.ai/library/qwen3.6/latest')


def read_manifest():
    raw = MANIFEST_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('The retained model manifest differs from the pinned study digest')
    manifest = json.loads(raw)
    if manifest.get('schemaVersion') != 2:
        raise ValueError('Unsupported model manifest schema')
    blobs = [manifest['config'], *manifest['layers']]
    for blob in blobs:
        digest, size = blob['digest'], blob['size']
        if (not digest.startswith('sha256:') or len(digest) != 71
                or any(c not in '0123456789abcdef' for c in digest[7:])
                or not isinstance(size, int) or isinstance(size, bool) or size <= 0):
            raise ValueError('Unsafe or incomplete model blob identity')
    if len({blob['digest'] for blob in blobs}) != len(blobs):
        raise ValueError('Duplicate model blob identity')
    return raw, blobs


def contained(root, relative):
    target = root / relative
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError('Model artifact path is a symlink or escapes the dedicated directory')
    return target


def verify_blob(path, blob):
    if not path.is_file() or path.stat().st_size != blob['size']:
        raise ValueError('Model blob is missing or has the wrong byte length: ' + str(path))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != blob['digest'][7:]:
        raise ValueError('Model blob SHA-256 mismatch: ' + str(path))


def stage(models_dir, *, verify_only=False):
    models_dir = models_dir.expanduser().resolve()
    raw, blobs = read_manifest()
    named_manifest = contained(models_dir, MANIFEST_RELATIVE)
    if named_manifest.exists() and named_manifest.read_bytes() != raw:
        raise ValueError('Refusing to replace a different named model manifest; use a dedicated directory')
    if verify_only and not named_manifest.is_file():
        raise ValueError('The pinned named model manifest has not been installed')
    verified = []
    for blob in blobs:
        target = contained(models_dir, Path('blobs') / blob['digest'].replace(':', '-'))
        if target.exists() or verify_only:
            # Existing corrupt files are not silently replaced. The caller can
            # inspect them and explicitly remove them before a new staging run.
            verify_blob(target, blob)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = contained(models_dir, target.relative_to(models_dir).with_name(target.name + '.partial'))
            if partial.exists() and (not partial.is_file() or partial.stat().st_size > blob['size']):
                raise ValueError('Invalid resumable partial model blob: ' + str(partial))
            if not partial.exists() or partial.stat().st_size < blob['size']:
                subprocess.run(['curl', '--fail', '--location', '--proto', '=https',
                    '--proto-redir', '=https', '--retry', '5', '--retry-all-errors', '--retry-delay', '2',
                    '--connect-timeout', '30', '--speed-time', '120', '--speed-limit', '1024',
                    '--continue-at', '-', '--output', str(partial),
                    REGISTRY_BLOBS + blob['digest']], check=True)
            verify_blob(partial, blob)
            # Hard-link publication cannot overwrite a blob created by another
            # process while this one was downloading or verifying.
            os.link(partial, target)
            partial.unlink()
        verified.append({'digest': blob['digest'], 'bytes': blob['size'],
                         'path': str(target), 'verified': True})
    if not named_manifest.exists():
        named_manifest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=named_manifest.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        try:
            os.link(temporary, named_manifest)
        finally:
            temporary.unlink()
    return {'model': MODEL, 'manifest_sha256': MANIFEST_SHA256,
            'manifest_bytes': len(raw), 'manifest_path': str(named_manifest),
            'registry_blobs': REGISTRY_BLOBS, 'blobs': verified,
            'scope': 'Verified model staging only; no inference or capacity measurement.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', required=True, type=Path,
                        help='Dedicated OLLAMA_MODELS directory; shared installations are not modified')
    parser.add_argument('--verify-only', action='store_true',
                        help='Verify all existing artifacts without downloading or installing')
    args = parser.parse_args()
    print(json.dumps(stage(args.models_dir, verify_only=args.verify_only), indent=2))


if __name__ == '__main__':
    main()
