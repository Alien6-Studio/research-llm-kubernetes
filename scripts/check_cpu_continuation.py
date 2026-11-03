"""Replay the complete native baseline from lossless files, without inference."""
import argparse
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'v2-ollama-fra6s004-cpu-continuation'
PROTOCOL = Path('protocol/ollama-fra6s004-cpu-continuation/manifest.json')
COLLECTION = Path('archives') / PREFIX / 'collection-manifest.json'
INDEX = Path('archives/cpu-native-baseline/evidence-index.json')
PROTOCOL_SHA = 'b44236e661a89971c427f4fe712895031c9f896aa7629c94e7e86420aba0274d'
COLLECTION_SHA = '0f9a6efa40ab853d261e5df3a49ab405b08310c315b2bbe9d049a5f2f8560db7'
DIAGNOSTIC_INDEXES = {
    'archives/v2-ollama-dell-cpu-memory-excluded/evidence-index.json': 'd39bebc1e25c5810d6a0ef95080c009eb1ae442f8fc6f2f74832f5f52cc1d0f9',
    'archives/v2-ollama-fra6s004-cpu-confirmation-planning/evidence-index.json': '662ad0c6fe9a3446c53be8d888a63fa716acdb5cce6444b9552e214db24f4420',
    'archives/v2-ollama-fra6s004-cpu-inspection-excluded/evidence-index.json': '03402b232f82c5c6785349979fe26e4e253d204a616b94bd16dbbc4b98c3c285',
}
sys.path.insert(0, str(ROOT))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(root, name):
    name = str(name)
    relative = PurePosixPath(name)
    if relative.is_absolute() or relative.as_posix() != name or not relative.parts or '..' in relative.parts:
        raise ValueError('Expected a canonical repository-relative evidence path')
    target = root / name
    for path in (target, *target.parents):
        if path == root: break
        if path.is_symlink(): raise ValueError('Evidence paths must not contain symbolic links')
    return target


def expected_files(root):
    protocol_raw, collection_raw = (root / PROTOCOL).read_bytes(), (root / COLLECTION).read_bytes()
    if digest(protocol_raw) != PROTOCOL_SHA or digest(collection_raw) != COLLECTION_SHA:
        raise ValueError('Frozen protocol or original collection inventory changed')
    protocol, collection = json.loads(protocol_raw), json.loads(collection_raw)
    rows = []
    for group, entries in (('prior', protocol['prior']['files']), ('continuation', collection['files'])):
        for row in entries:
            rows.append({'source_relative_path': row['path'], 'decoded_bytes': row['bytes'],
                         'decoded_sha256': row['sha256'], 'group': group})
    if (len(protocol['prior']['files']) != 19 or len(collection['files']) != 185
            or len({row['source_relative_path'] for row in rows}) != 204):
        raise ValueError('All nineteen prefix files and 185 continuation files are required')
    return protocol, sorted(rows, key=lambda row: row['source_relative_path'])


def compressed_bytes(raw):
    output = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=output, mode='wb', compresslevel=9, mtime=0) as stream:
        stream.write(raw)
    return output.getvalue()


def write_immutable(path, raw):
    if path.exists():
        if path.read_bytes() != raw: raise ValueError('Existing evidence differs: ' + str(path))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    path.chmod(0o444)
    return True


def pack(root):
    protocol, expected = expected_files(root)
    from v2.continuation import check_manifest
    check_manifest(root / PROTOCOL, root=root, deep=True)
    files = []
    for row in expected:
        raw = safe_path(root, row['source_relative_path']).read_bytes()
        if len(raw) != row['decoded_bytes'] or digest(raw) != row['decoded_sha256']:
            raise ValueError('Original evidence changed: ' + row['source_relative_path'])
        name = 'archives/cpu-native-baseline/files/' + row['source_relative_path'] + '.gz'
        target = safe_path(root, name)
        if target.exists():
            encoded = target.read_bytes()
            if gzip.decompress(encoded) != raw or encoded[4:8] != b'\0' * 4:
                raise ValueError('Existing compressed evidence differs: ' + name)
        else:
            encoded = compressed_bytes(raw); write_immutable(target, encoded)
        files.append({**row, 'path': name, 'bytes': len(encoded), 'sha256': digest(encoded)})
    value = {'schema': 'alien6-cpu-native-evidence-v1', 'source_id': protocol['source_id'],
             'git_base': protocol['git_base'], 'protocol': {'path': PROTOCOL.as_posix(), 'sha256': PROTOCOL_SHA},
             'collection_inventory': {'path': COLLECTION.as_posix(), 'sha256': COLLECTION_SHA},
             'compression': 'Lossless gzip; header mtime is zero; decoded bytes and original collection times are unchanged.',
             'files': files}
    write_immutable(root / INDEX, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
    return {'compressed_files': len(files), 'compressed_bytes': sum(row['bytes'] for row in files),
            'decoded_bytes': sum(row['decoded_bytes'] for row in files),
            'largest_compressed_file': max(row['bytes'] for row in files), 'index': INDEX.as_posix()}


def materialize_entry(root, row):
    source, target = safe_path(root, row['path']), safe_path(root, row['source_relative_path'])
    encoded = source.read_bytes()
    if len(encoded) != row['bytes'] or digest(encoded) != row['sha256'] or encoded[4:8] != b'\0' * 4:
        raise ValueError('Compressed evidence checksum, size or header differs: ' + row['path'])
    raw = gzip.decompress(encoded)
    if len(raw) != row['decoded_bytes'] or digest(raw) != row['decoded_sha256']:
        raise ValueError('Decoded evidence checksum or size differs: ' + row['path'])
    if target.exists():
        if target.read_bytes() != raw: raise ValueError('Existing decoded evidence differs: ' + row['source_relative_path'])
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.evidence-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o444); os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return True


def verify_diagnostics(root):
    """Preserve incomplete operational contexts without treating them as trials."""
    count = 0
    for name, expected in DIAGNOSTIC_INDEXES.items():
        raw = safe_path(root, name).read_bytes()
        if digest(raw) != expected: raise ValueError('Excluded diagnostic index changed: ' + name)
        index = json.loads(raw)
        seen = set()
        for row in index['files']:
            if row['path'] in seen or not row['path'].startswith(str(Path(name).parent) + '/'):
                raise ValueError('Duplicate or out-of-cohort diagnostic archive')
            seen.add(row['path'])
            encoded = safe_path(root, row['path']).read_bytes()
            if digest(encoded) != row['sha256'] or len(encoded) != row['bytes']:
                raise ValueError('Excluded diagnostic compressed bytes differ')
            decoded = gzip.decompress(encoded)
            if digest(decoded) != row['decoded_sha256'] or len(decoded) != row['decoded_bytes']:
                raise ValueError('Excluded diagnostic decoded bytes differ')
            count += 1
    return {'excluded_operational_indexes': len(DIAGNOSTIC_INDEXES), 'excluded_operational_files': count,
            'diagnostic_scope': 'Exact byte identities only; incomplete operational source context is not a qualified collection.'}


def restore(root):
    protocol, expected = expected_files(root)
    value = json.loads((root / INDEX).read_bytes())
    if (value.get('schema') != 'alien6-cpu-native-evidence-v1' or value.get('source_id') != protocol['source_id']
            or value.get('git_base') != protocol['git_base']
            or value.get('protocol') != {'path': PROTOCOL.as_posix(), 'sha256': PROTOCOL_SHA}
            or value.get('collection_inventory') != {'path': COLLECTION.as_posix(), 'sha256': COLLECTION_SHA}):
        raise ValueError('Published evidence index identity differs')
    rows = value['files']
    expected_by_path = {row['source_relative_path']: row for row in expected}
    if len(rows) != 204 or {row['source_relative_path'] for row in rows} != set(expected_by_path):
        raise ValueError('Published evidence index has missing or duplicate decoded paths')
    for row in rows:
        if ({key: row[key] for key in expected[0]} != expected_by_path[row['source_relative_path']]
                or row['path'] != 'archives/cpu-native-baseline/files/' + row['source_relative_path'] + '.gz'):
            raise ValueError('Published evidence index differs from an original raw SHA')
    restored = sum(materialize_entry(root, row) for row in rows)
    # The calibration is already published separately; restore its canonical path
    # for the frozen CPU method without duplicating it in this 204-file archive.
    pilot = json.loads((root / 'archives/v2-ollama-fra6s004-cpu-confirmation-pilot/manifest.json').read_bytes())['files']
    if (len(pilot) != 1 or pilot[0]['source_relative_path'] != 'runs/v2-ollama-fra6s004-cpu-confirmation-pilot/pilot.json'
            or pilot[0]['decoded_sha256'] != '1f081e1a6174a7dcdbe479b06f7df4b18661bcd811bb0f8ac876b64a4bf10224'):
        raise ValueError('Retained calibration pilot identity differs')
    materialize_entry(root, pilot[0])
    return {**verify_diagnostics(root), 'verified_compressed_files': len(rows), 'restored_raw_files': restored,
            'prior_files': 19, 'continuation_files': 185, 'calibration_pilot_restored_or_identical': True}


def compare_reports(stored, replayed):
    for value in (stored, replayed):
        parts = str(value.get('python', '')).split('.')
        if len(parts) != 3 or parts[:2] != ['3', '12'] or not all(part.isdigit() for part in parts):
            raise ValueError('Both report provenance versions must be Python 3.12 patch releases')
    left = {key: value for key, value in stored.items() if key != 'python'}
    right = {key: value for key, value in replayed.items() if key != 'python'}
    if left != right: raise ValueError('Scientific report differs from archived observations')
    return digest(json.dumps(left, sort_keys=True, separators=(',', ':')).encode())


def csv_bytes(report):
    from scripts.report_cpu_continuation import CSV_FIELDS
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator='\n'); writer.writeheader()
    for row in report['trials']:
        writer.writerow({key: json.dumps(row.get(key), sort_keys=True, separators=(',', ':'))
                         if isinstance(row.get(key), (dict, list)) else row.get(key) for key in CSV_FIELDS})
    return output.getvalue().encode()


def check(root):
    restored = restore(root)
    from scripts.report_cpu_continuation import build_report
    from v2.verify import verify_standalone_warmup
    manifest = json.loads((root / PROTOCOL).read_bytes())
    replayed = build_report(root / PROTOCOL, root=root)
    stored = json.loads((root / 'analysis' / PREFIX / 'results.json').read_bytes())
    scientific_sha = compare_reports(stored, replayed)
    if csv_bytes(replayed) != (root / 'analysis' / PREFIX / 'trials.csv').read_bytes():
        raise ValueError('Trial CSV is not byte-identical to deterministic replay')
    rows = replayed['trials']
    if (replayed['observed_trials'] != 45 or replayed['continuation_observed'] != 28
            or replayed['status_counts'] != {'certified_observation': 44, 'native_completion_uncertain': 1}
            or any(row['observation_status'] != 'certified_observation' for row in rows[17:])
            or replayed['qualified'] or replayed['parent_block_qualified'] or replayed['included_for_inference']
            or sum(row['native_completed'] for row in rows if row['native_completion'] == 'certified') != 1490):
        raise ValueError('Complete observation grid, certification or interpretation boundary changed')
    spec = importlib.util.spec_from_file_location('baseline_recovery', root / 'infra/recover-fra6s004-inference.py')
    recovery = importlib.util.module_from_spec(spec); spec.loader.exec_module(recovery)
    proofs = [json.loads((root / manifest['run_root'] / 'initial-recovery.json').read_bytes())]
    warmups = 0
    for row in manifest['trials']:
        proofs.append(json.loads((root / row['recovery_path']).read_bytes()))
        warmup = json.loads((root / row['warmup_path']).read_bytes())
        warmups += verify_standalone_warmup(warmup)
        if any(observation['status'] != 200 or observation['raw'].get('done') is not True for observation in warmup['observations']):
            raise ValueError('Technical warmup lacks a complete native response')
    for proof in proofs: recovery.validate_proof(proof)
    if len(proofs) != 29 or len({proof['new_epoch'] for proof in proofs}) != 29 or warmups != 112:
        raise ValueError('Reset or technical warmup lifecycle is incomplete')
    return {**restored, 'source_id': replayed['source_id'], 'observations': 45,
            'certified_observations': 44, 'native_completion_uncertain': 1, 'new_certified_observations': 28,
            'complete_native_responses_in_certified_trials': 1490, 'reset_proofs': 29, 'technical_warmups': 112,
            'scientific_report_sha256': scientific_sha, 'csv_byte_identical': True,
            'collected_python': stored['python'], 'replayed_python': replayed['python'],
            'scope': 'Deterministic evidence replay only; no inference request, qualified aggregate or causal pooling.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--pack', action='store_true', help='Create immutable lossless per-file publication copies')
    mode.add_argument('--restore-only', action='store_true', help='Verify gzip identities and materialize ignored raw inputs')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if not __debug__ or sys.version_info[:2] != (3, 12): parser.error('Use Python 3.12 without optimization for the frozen replay')
    root = args.root.resolve()
    result = pack(root) if args.pack else restore(root) if args.restore_only else check(root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__': main()
