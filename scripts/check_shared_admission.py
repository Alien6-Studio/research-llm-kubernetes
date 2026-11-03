"""Check the frozen paired protocol, or replay its complete archived collection."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MANIFEST = 'protocol/shared-admission/manifest.json'
MANIFEST_SHA256 = '10133709b44e596247bfc11f5e974787a93a3618a3a6b0c06d4fef43c37461de'
SOURCE_ID = 'sha256:025dad4d2ba37487cc21ab4c90488534b8dafe0a3fc1cb25833f558b7191a1cd'
COLLECTION = 'archives/v2-ollama-shared-admission/collection-manifest.json'
COLLECTION_SHA256 = '7cef7c4fada2b48fad4e02d0787360bd3939b170d0b1b2437dac4f39dce72c6d'
ORIGINAL_ARCHIVE_SHA256 = 'fc5f9fb7198d1a6e2d1d23acff4cf62ffd6341ea580507f81d5c95a43392afe0'
PUBLIC_INDEX = 'archives/shared-admission/evidence-index.json'
EXPECTED_RAW_FILES = 116
REPORT_DIRECTORY = 'analysis/v2-ollama-shared-admission'
CSV_FIELDS = ('case_id', 'pair_id', 'replicas', 'profile', 'repetition', 'mode', 'capacity',
    'status', 'primary_observable', 'resource_qualified', 'measurement_arrivals',
    'timely_correct_arrivals', 'timely_correct_fraction', 'timely_correct_per_second',
    'http429_arrivals', 'execution_issues', 'native_issues', 'counts')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(root, name):
    relative = PurePosixPath(name)
    if (not relative.parts or relative.is_absolute() or relative.as_posix() != name
            or '\\' in name or '..' in relative.parts):
        raise ValueError('Use canonical repository-relative evidence paths')
    path = root / name
    for component in (path, *path.parents):
        if component == root: break
        if component.is_symlink(): raise ValueError('Evidence paths must not contain symbolic links')
    return path


def protocol_check(root):
    from v2.admission_study import check_study
    raw = inside(root, MANIFEST).read_bytes()
    if sha(raw) != MANIFEST_SHA256:
        raise ValueError('The exact prospective study manifest changed')
    manifest = check_study(inside(root, MANIFEST), root=root)
    if manifest['study_source_id'] != SOURCE_ID:
        raise ValueError('The recorded extended study source changed')
    if len(manifest['cases']) != 24 or len({case['pair_id'] for case in manifest['cases']}) != 12:
        raise ValueError('The complete twenty-four-arm paired protocol is required')
    return manifest


def data_availability(root, manifest):
    available = [case['case_id'] for case in manifest['cases'] if inside(root, case['result_path']).is_file()]
    missing = [case['case_id'] for case in manifest['cases'] if case['case_id'] not in available]
    report_files = [REPORT_DIRECTORY + '/results.json', REPORT_DIRECTORY + '/trials.csv', COLLECTION, PUBLIC_INDEX]
    artifacts = [name for name in report_files if inside(root, name).exists()]
    return {'available_arm_files': len(available), 'planned_arms': 24, 'missing_case_ids': missing,
            'data_state': 'absent' if not available else 'partial_unverified' if missing else 'present_unverified',
            'publication_artifacts_present': artifacts,
            'final_validation_required': bool(available or artifacts)}


def verify_collection_inventory(root, manifest):
    inventory = json.loads(inside(root, COLLECTION).read_bytes())
    if not re.fullmatch('[0-9a-f]{64}', inventory.get('archive_sha256', '')):
        raise ValueError('The original export archive identity is unavailable')
    rows = inventory.get('files')
    if not isinstance(rows, list) or not rows:
        raise ValueError('The complete original export file inventory is required')
    prefix = manifest['run_root'] + '/'
    names = []
    for row in rows:
        name = row['path']
        if (not name.startswith(prefix) or type(row.get('bytes')) is not int or row['bytes'] < 0
                or not re.fullmatch('[0-9a-f]{64}', row.get('sha256', ''))):
            raise ValueError('Invalid original export inventory entry')
        raw = inside(root, name).read_bytes()
        if len(raw) != row['bytes'] or sha(raw) != row['sha256']:
            raise ValueError('Archived raw evidence differs: ' + name)
        names.append(name)
    actual = {path.relative_to(root).as_posix() for path in inside(root, manifest['run_root']).rglob('*') if path.is_file()}
    if len(names) != len(set(names)) or set(names) != actual:
        raise ValueError('The export inventory must cover every retained raw file exactly once')
    required = {manifest['run_root'] + '/' + name for name in
                ('initial-recovery.json', 'network/control.json', 'qualification/block.json',
                 'qualification/manifest.json', 'study-control-binding.json')}
    for case in manifest['cases']:
        required.update(case[key] for key in ('recovery_path', 'warmup_path', 'result_path'))
        required.add(str(PurePosixPath(case['result_path']).parent / 'manifest.json'))
    if not required.issubset(names):
        raise ValueError('The collection lacks required controls or per-arm lifecycle evidence')
    return {'raw_files': len(names), 'inventory_sha256': sha(inside(root, COLLECTION).read_bytes()),
            'original_archive_sha256': inventory['archive_sha256']}


def published_inputs(root, manifest):
    raw = inside(root, COLLECTION).read_bytes()
    if sha(raw) != COLLECTION_SHA256:
        raise ValueError('The original paired export inventory bytes changed')
    collection = json.loads(raw)
    rows = collection['files']
    if collection.get('archive_sha256') != ORIGINAL_ARCHIVE_SHA256 or len(rows) != EXPECTED_RAW_FILES:
        raise ValueError('The complete original paired export identity or file count differs')
    names = set()
    expected = []
    for row in rows:
        name = row['path']
        inside(root, name)
        if (name in names or not name.startswith(manifest['run_root'] + '/')
                or type(row.get('bytes')) is not int or row['bytes'] < 0
                or not re.fullmatch('[0-9a-f]{64}', row.get('sha256', ''))):
            raise ValueError('The original paired export contains a duplicate or invalid raw identity')
        names.add(name)
        expected.append({'source_relative_path': name, 'decoded_bytes': row['bytes'],
                         'decoded_sha256': row['sha256']})
    return {'path': COLLECTION, 'sha256': sha(raw)}, sorted(expected,key=lambda row:row['source_relative_path'])


def pack(root):
    """Preserve exact final raw bytes; never move or replace the originals."""
    from scripts.check_cpu_continuation import compressed_bytes, write_immutable
    root = Path(root).resolve()
    checked = check(root, 'final', restore_inputs=False)
    manifest = protocol_check(root)
    collection, expected = published_inputs(root, manifest)
    rows = []
    for row in expected:
        raw = inside(root, row['source_relative_path']).read_bytes()
        if len(raw) != row['decoded_bytes'] or sha(raw) != row['decoded_sha256']:
            raise ValueError('Original paired raw evidence changed')
        name = 'archives/shared-admission/files/' + row['source_relative_path'] + '.gz'
        target = inside(root, name)
        if target.exists():
            encoded = target.read_bytes()
            if len(encoded) >= 100 * 1024 ** 2 or gzip.decompress(encoded) != raw or encoded[4:8] != b'\0' * 4:
                raise ValueError('Existing paired compressed evidence differs')
        else:
            encoded = compressed_bytes(raw)
            if len(encoded) >= 100 * 1024 ** 2:
                raise ValueError('An individually compressed evidence file exceeds the Git blob limit')
            write_immutable(target, encoded)
        rows.append({**row, 'path': name, 'bytes': len(encoded), 'sha256': sha(encoded)})
    index = {'schema': 'alien6-shared-admission-evidence-v1', 'study_source_id': SOURCE_ID,
        'git_base': manifest['git_base'], 'protocol': {'path': MANIFEST, 'sha256': MANIFEST_SHA256},
        'collection_inventory': collection, 'original_archive_sha256': ORIGINAL_ARCHIVE_SHA256,
        'compression': 'Lossless gzip; zero header mtime; original raw bytes and collection times are unchanged.',
        'files': rows}
    write_immutable(inside(root,PUBLIC_INDEX),(json.dumps(index,indent=2,sort_keys=True)+'\n').encode())
    return {'compressed_files':len(rows),'compressed_bytes':sum(row['bytes'] for row in rows),
        'decoded_bytes':sum(row['decoded_bytes'] for row in rows),'largest_compressed_file':max(row['bytes'] for row in rows),
        'index':PUBLIC_INDEX,'index_sha256':sha(inside(root,PUBLIC_INDEX).read_bytes()),
        'study_completion_verified':checked['study_completion_verified']}


def restore(root):
    """Restore only exact indexed raw identities, refusing any differing file."""
    from scripts.check_cpu_continuation import materialize_entry
    root = Path(root).resolve()
    manifest = protocol_check(root)
    collection, expected = published_inputs(root,manifest)
    index = json.loads(inside(root,PUBLIC_INDEX).read_bytes())
    if (index.get('schema') != 'alien6-shared-admission-evidence-v1' or index.get('study_source_id') != SOURCE_ID
            or index.get('git_base') != manifest['git_base']
            or index.get('protocol') != {'path':MANIFEST,'sha256':MANIFEST_SHA256}
            or index.get('collection_inventory') != collection
            or index.get('original_archive_sha256') != ORIGINAL_ARCHIVE_SHA256):
        raise ValueError('Published paired evidence index differs from the original export context')
    expected_by_path={row['source_relative_path']:row for row in expected}
    rows=index['files']
    if len(rows)!=EXPECTED_RAW_FILES or {row['source_relative_path'] for row in rows} != set(expected_by_path):
        raise ValueError('Published paired evidence has missing or duplicate raw paths')
    for row in rows:
        expected_row=expected_by_path[row['source_relative_path']]
        if ({key:row[key] for key in expected_row} != expected_row
                or row['path'] != 'archives/shared-admission/files/' + row['source_relative_path'] + '.gz'):
            raise ValueError('Published paired evidence differs from an original raw SHA')
    restored=sum(materialize_entry(root,row) for row in rows)
    return {'verified_compressed_files':len(rows),'restored_raw_files':restored,
            'index_sha256':sha(inside(root,PUBLIC_INDEX).read_bytes()),'inference_executed':False}


def validate_complete_report(report, manifest):
    rows = report.get('trials', [])
    expected = manifest['cases']
    if (report.get('schema') != 'alien6-shared-admission-analysis-v1'
            or report.get('study_source_id') != SOURCE_ID or report.get('complete') is not True
            or report.get('all_primary_observable') is not True
            or report.get('controls', {}).get('qualified') is not True
            or report.get('confirmatory_inference') is not False
            or report.get('parent_observations_pooled') is not False
            or [row.get('case_id') for row in rows] != [case['case_id'] for case in expected]
            or len(rows) != 24):
        raise ValueError('Final replay requires all twenty-four observable arms and qualified fresh controls')
    for row, case in zip(rows, expected, strict=True):
        if (row.get('pair_id') != case['pair_id'] or row.get('mode') != case['mode']
                or row.get('primary_observable') is not True or row.get('execution_issues') != []
                or row.get('status') not in ('observed', 'resource_censored')
                or type(row.get('resource_qualified')) is not bool
                or (row['status'] == 'observed') != row['resource_qualified']):
            raise ValueError('An arm has missing coverage, an execution defect or a changed paired identity')
    pairs = report.get('pairs', [])
    if (len(pairs) != 12 or len({pair.get('pair_id') for pair in pairs}) != 12
            or {pair.get('pair_id') for pair in pairs} != {case['pair_id'] for case in expected}
            or any(pair.get('complete') is not True for pair in pairs)):
        raise ValueError('All twelve pairs must retain both observable arms')
    return dict(Counter(row['status'] for row in rows))


def csv_bytes(report):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS); writer.writeheader()
    for row in report['trials']:
        writer.writerow({key: json.dumps(row[key], sort_keys=True)
                         if isinstance(row.get(key), (list, dict)) else row.get(key) for key in CSV_FIELDS})
    return output.getvalue().encode()


def check(root, mode='protocol', *, restore_inputs=True):
    root = Path(root).resolve()
    if mode not in ('protocol', 'final'): raise ValueError('Select protocol or final verification explicitly')
    manifest = protocol_check(root)
    restored = restore(root) if mode == 'final' and restore_inputs and inside(root,PUBLIC_INDEX).is_file() else None
    availability = data_availability(root, manifest)
    result = {'schema': 'alien6-shared-admission-check-v1', 'mode': mode,
              'protocol_valid': True, 'study_source_id': SOURCE_ID, 'manifest_sha256': MANIFEST_SHA256,
              'paired_conditions': 12, 'experimental_data_verified': False,
              'study_completion_verified': False, 'availability': availability,
              'confirmatory_inference': False, 'parent_observations_pooled': False}
    if mode == 'protocol':
        result['interpretation'] = 'Protocol/source validation only. Missing observations are explicit; a passing code check does not mean that the study has finished.'
        return result
    if availability['available_arm_files'] != 24:
        raise ValueError('Final verification requires all 24 observed arm files; available=' + str(availability['available_arm_files']))
    inventory = verify_collection_inventory(root, manifest)
    from v2.admission_study import analyze_study
    replayed = analyze_study(inside(root, MANIFEST), root=root)
    statuses = validate_complete_report(replayed, manifest)
    stored = json.loads(inside(root, REPORT_DIRECTORY + '/results.json').read_bytes())
    if stored != replayed:
        raise ValueError('Published paired report differs from deterministic replay')
    csv_raw = inside(root, REPORT_DIRECTORY + '/trials.csv').read_bytes()
    if csv_bytes(replayed) != csv_raw:
        raise ValueError('Published paired CSV differs from deterministic replay')
    result.update(experimental_data_verified=True, study_completion_verified=True,
        observation_statuses=statuses, evidence=inventory,
        report_sha256=sha(inside(root, REPORT_DIRECTORY + '/results.json').read_bytes()),
        csv_sha256=sha(csv_raw),
        interpretation='All 24 arms replayed with 12 complete pairs. Native resource censoring remains visible; these exploratory observations do not establish a production SLO or a general library benefit.')
    if restored is not None: result['lossless_inputs'] = restored
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation=parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--mode', choices=('protocol', 'final'))
    operation.add_argument('--pack', action='store_true')
    operation.add_argument('--restore-only', action='store_true')
    parser.add_argument('--github-summary', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or sys.flags.optimize:
        parser.error('Use Python 3.12 with assertions enabled')
    try:
        result = pack(ROOT) if args.pack else restore(ROOT) if args.restore_only else check(ROOT, args.mode)
    except (OSError, ValueError, TypeError, KeyError, AssertionError, RuntimeError) as error:
        print(json.dumps({'mode': args.mode, 'verified': False, 'error': str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    if args.github_summary and args.mode and os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
            summary.write('### Shared-admission ' + ('final replay' if args.mode == 'final' else 'protocol check') + '\n\n')
            if result['study_completion_verified']:
                summary.write('All 24 arms and 12 pairs replayed. Results remain exploratory.\n')
            else:
                summary.write('Protocol and frozen source verified. **Experimental completion has not been verified.**\n\n')
                summary.write(f'Available raw arm files: {result["availability"]["available_arm_files"]}/24.\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
