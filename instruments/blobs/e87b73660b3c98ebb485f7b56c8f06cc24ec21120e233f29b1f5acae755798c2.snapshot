"""Prospective paired study of one shared native in-flight budget."""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone
import random
import sys

from v2.identity import MEASURED_FILES, capture, verify_registered
from v2.model import validate_pilot
from v2.plan import ROOT, canonical
from v2.run import percentile

BASE_SOURCE_ID = 'sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f'
CALIBRATION_SOURCE_ID = 'sha256:c811876fbf14c2af54a18f600044368dd0a92561497e0bbdf8d02c2a6bd86c3b'
PILOT_PATH = 'runs/v2-ollama-fra6s004-cpu-confirmation-pilot/pilot.json'
PILOT_SHA256 = '1f081e1a6174a7dcdbe479b06f7df4b18661bcd811bb0f8ac876b64a4bf10224'
PARENT_MANIFEST_SHA256 = 'b44236e661a89971c427f4fe712895031c9f896aa7629c94e7e86420aba0274d'
PARENT_PLAN_SHA256 = 'b1917a52bc70ed7ef83f8d1f071b14de62d9986184f23d4072536dfb7d1117ce'
ORDER_SEED = 2026111501
STUDY_FILES = (*MEASURED_FILES,
    'v2/admission_proxy.py', 'v2/admission_driver.py', 'v2/admission_study.py', 'v2/admission_certificate.py',
    'infra/recover-fra6s004-admission.py', 'infra/run-fra6s004-admission.py',
    'infra/alien6-admission-proxy.service', 'infra/alien6-ollama.service',
    'infra/alien6-ollama-monitor.service', 'infra/recover-fra6s004-inference.py',
    'infra/run-fra6s004-continuation.py', 'infra/run-dell-cpu-campaign.py',
    'scripts/observe_study_host.py', 'v2/verify.py', 'v2/cpu_campaigns.py',
    'studies/shared-admission/PROTOCOL.md')
PROTOCOL = {
    'replicas': [1, 8], 'loads': ['near', 'high'], 'modes': ['bypass', 'global'],
    'repetitions': [0, 1, 2], 'trials': 24, 'pairs': 12, 'order_seed': ORDER_SEED,
    'measurement_seconds': 335, 'client_deadline_seconds': 10,
    'model_call_timeout_seconds': 120, 'native_forward_timeout_seconds': 180,
    'collector_budget_seconds': 900, 'reset_before_every_trial': True,
    'warmup_responses': 4, 'network_repetitions': 3, 'qualification_trials': 12,
    'queue_policy': 'reject_immediately', 'permit_release': 'successful_native_terminal_only',
    'uncertain_permit': 'quarantine_until_certified_reset',
    'primary_endpoint': 'correct client response within10s / all scheduled measurement arrivals',
    'native_uncertainty': 'Retain observed client outcomes; censor native resource lifetime.',
    'inference': 'Three paired trace repetitions per replica/load condition; exploratory only. No request pooling or confirmatory library claim.',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(root, value):
    root, value = Path(root).resolve(), Path(value)
    path = (root / value).resolve()
    if '..' in value.parts or not path.is_relative_to(root) or path == root:
        raise ValueError('Study paths must stay inside the repository')
    return path


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def file_record(root, path):
    root = Path(root).resolve()
    path = inside(root, path); raw = path.read_bytes()
    return {'path': path.relative_to(root).as_posix(), 'sha256': digest(raw), 'bytes': len(raw)}


def select_capacity(pilot):
    """Use only the original closed-loop pilot, never treatment outcomes."""
    validate_pilot(pilot)
    if pilot.get('source_id') != CALIBRATION_SOURCE_ID:
        raise ValueError('Capacity selection requires the original calibration source')
    values = []
    for setting in pilot['results']:
        latencies = [row['finished'] - row['began'] for row in setting['observations']]
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in latencies):
            raise ValueError('Pilot request latencies must be finite and nonnegative')
        values.append({'concurrency': setting['concurrency'], 'requests': len(latencies),
            'p50_seconds': percentile(latencies, .5), 'p95_seconds': percentile(latencies, .95),
            'within_deadline': sum(value <= 10 for value in latencies), 'request_seconds': latencies})
    eligible = [row['concurrency'] for row in values if row['p95_seconds'] <= 10]
    return {'capacity': max(eligible) if eligible else min(row['concurrency'] for row in values),
            'pilot_deadline_feasible': bool(eligible), 'deadline_seconds': 10,
            'rule': 'Largest tested concurrency with request p95 <=10s; otherwise the smallest tested concurrency, explicitly not SLO-qualified.',
            'quantile': 'Pinned BBA percentile implementation; with16 samples the p95 is the maximum.',
            'timing_scope': 'Pilot HTTP elapsed after acquiring its closed-loop concurrency semaphore; not open-loop end-to-end capacity.',
            'groups': values}


def verify_sources(root, source_id, git_base, *, live=False):
    """Offline replay retains the actual archived Git context rather than HEAD."""
    root = Path(root).resolve()
    base = verify_registered(root, BASE_SOURCE_ID)
    study = verify_registered(root, source_id)
    if source_id == BASE_SOURCE_ID or set(study['files']) != set(STUDY_FILES):
        raise ValueError('The study needs its complete distinct extended source snapshot')
    for name in MEASURED_FILES:
        if study['files'][name]['sha256'] != base['files'][name]['sha256']:
            raise ValueError('The frozen BBA measurement instrument changed: ' + name)
    snapshot = capture(root, files=STUDY_FILES, git_base=None if live else git_base)
    if snapshot.source_id != source_id or snapshot.git_base != git_base:
        raise ValueError('Study source bytes or live Git base differ from the recorded study')
    return snapshot


def inputs(root, parent_manifest_path):
    parent_record = file_record(root, parent_manifest_path)
    if parent_record['sha256'] != PARENT_MANIFEST_SHA256:
        raise ValueError('Use the exact retained exploratory continuation manifest')
    parent_manifest = json.loads(inside(root, parent_manifest_path).read_bytes())
    plan_record = file_record(root, parent_manifest['parent_plan']['path'])
    if plan_record['sha256'] != PARENT_PLAN_SHA256:
        raise ValueError('The original frozen workload plan changed')
    plan = json.loads(inside(root, plan_record['path']).read_bytes())
    raw_pilot = inside(root, PILOT_PATH).read_bytes()
    if digest(raw_pilot) != PILOT_SHA256:
        raise ValueError('The original52-response calibration bytes changed')
    pilot = json.loads(raw_pilot)
    selection = select_capacity(pilot)
    traces = {}
    for config in [config for block in plan['blocks'].values() for config in block]:
        reference = config['trace_reference']
        record = file_record(root, reference['path'])
        if record['sha256'] != reference['sha256']:
            raise ValueError('A retained workload trace changed')
        traces[record['path']] = record
    return parent_record, plan_record, plan, selection, [traces[path] for path in sorted(traces)]


def cases(parent_plan, capacity):
    """Shuffle paired conditions once; both arms use the same exact trace."""
    source = {}
    for config in parent_plan['blocks']['real-model']:
        if not config.get('direct_ollama') and config['replicas'] in (1, 8) and config['profile'] in ('near', 'high'):
            key = (config['replicas'], config['profile'], config['repetition'])
            if key in source:
                raise ValueError('Duplicate parent condition')
            if (config['policy'] != 'none' or config['measurement_seconds'] != 335
                    or config['deadline_seconds'] != 10 or config['provider']['mode'] != 'ollama'):
                raise ValueError('The retained worker condition differs from the fixed real-model study')
            source[key] = config
    expected = {(workers, load, rep) for workers in (1, 8) for load in ('near', 'high') for rep in (0, 1, 2)}
    if set(source) != expected:
        raise ValueError('All twelve paired parent conditions are required')
    rng = random.Random(ORDER_SEED); order = sorted(source); rng.shuffle(order)
    result = []
    for workers, load, rep in order:
        pair_id = f'{load}-{workers}-{rep}'
        modes = ['bypass', 'global']; rng.shuffle(modes)
        for mode in modes:
            original = source[(workers, load, rep)]
            config = copy.deepcopy(original)
            identifier = f'admission-{pair_id}-{mode}'
            config['trial_id'] = identifier
            result.append({'case_id': identifier, 'pair_id': pair_id, 'parent_trial_id': original['trial_id'],
                           'mode': mode, 'capacity': capacity, 'configuration': config})
    return result


def case_plan(case, source_id, git_base):
    return {'blocks': {'real-model': [copy.deepcopy(case['configuration'])]},
        'shared_admission': {'case_id': case['case_id'], 'pair_id': case['pair_id'],
            'parent_trial_id': case['parent_trial_id'], 'mode': case['mode'], 'capacity': case['capacity'],
            'base_source_id': BASE_SOURCE_ID, 'study_source_id': source_id, 'git_base': git_base,
            'queue_policy': PROTOCOL['queue_policy'], 'permit_release': PROTOCOL['permit_release']}}


def prepare_study(parent_manifest, out, *, study_source_id, run_root='runs/shared-admission', root=None):
    root = Path(root or ROOT).resolve(); target = inside(root, out); run = inside(root, run_root)
    if target.exists() or (target.parent / 'plans').exists() or run.exists():
        raise FileExistsError('A study requires fresh manifest, selected-plan and result paths')
    snapshot = capture(root, files=STUDY_FILES)
    verify_sources(root, study_source_id, snapshot.git_base, live=True)
    parent, plan_record, plan, selection, traces = inputs(root, parent_manifest)
    schedule = cases(plan, selection['capacity'])
    manifest = {'schema': 'alien6-shared-admission-study-v1', 'base_source_id': BASE_SOURCE_ID,
        'study_source_id': study_source_id, 'git_base': snapshot.git_base,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'parent_manifest': parent, 'parent_plan': plan_record,
        'calibration': {'path': PILOT_PATH, 'sha256': PILOT_SHA256, 'source_id': CALIBRATION_SOURCE_ID,
                        'git_base': json.loads(inside(root, PILOT_PATH).read_bytes())['git_base'], 'selection': selection},
        'protocol': copy.deepcopy(PROTOCOL), 'trace_files': traces,
        'source_files': [file_record(root, path) for path in sorted(STUDY_FILES)],
        'run_root': run.relative_to(root).as_posix(), 'cases': []}
    outputs = []
    for case in schedule:
        path = target.parent / 'plans' / (case['case_id'] + '.json')
        raw = encoded(case_plan(case, study_source_id, snapshot.git_base))
        directory = run / 'trials' / case['case_id']
        manifest['cases'].append({**case, 'plan_path': path.relative_to(root).as_posix(), 'plan_sha256': digest(raw),
            'epoch_directory': directory.relative_to(root).as_posix(),
            'recovery_path': (directory / 'recovery.json').relative_to(root).as_posix(),
            'warmup_path': (directory / 'warmup.json').relative_to(root).as_posix(),
            'result_directory': (directory / 'real-model').relative_to(root).as_posix(),
            'result_path': (directory / 'real-model' / (case['case_id'] + '.json')).relative_to(root).as_posix()})
        outputs.append((path, raw))
    (target.parent / 'plans').mkdir(parents=True, exist_ok=False)
    for path, raw in [*outputs, (target, encoded(manifest))]:
        with path.open('xb') as stream: stream.write(raw)
        path.chmod(0o444)
    return manifest


def check_study(path, *, root=None, live=False):
    root = Path(root or ROOT).resolve(); target = inside(root, path)
    manifest = json.loads(target.read_bytes())
    frozen = datetime.fromisoformat(manifest.get('created_utc', ''))
    if frozen.utcoffset() != timezone.utc.utcoffset(frozen):
        raise ValueError('Record the actual prospective UTC freeze time')
    if manifest.get('schema') != 'alien6-shared-admission-study-v1' or manifest.get('protocol') != PROTOCOL or manifest.get('base_source_id') != BASE_SOURCE_ID:
        raise ValueError('The prospective admission protocol changed')
    verify_sources(root, manifest['study_source_id'], manifest['git_base'], live=live)
    parent, plan_record, plan, selection, traces = inputs(root, manifest['parent_manifest']['path'])
    if (manifest['parent_manifest'] != parent or manifest['parent_plan'] != plan_record
            or manifest['calibration'] != {'path': PILOT_PATH, 'sha256': PILOT_SHA256, 'source_id': CALIBRATION_SOURCE_ID,
                'git_base': json.loads(inside(root, PILOT_PATH).read_bytes())['git_base'], 'selection': selection}
            or manifest['trace_files'] != traces
            or manifest['source_files'] != [file_record(root, p) for p in sorted(STUDY_FILES)]):
        raise ValueError('Study source, calibration or frozen workload identity changed')
    expected = cases(plan, selection['capacity'])
    if len(manifest.get('cases', [])) != len(expected):
        raise ValueError('The complete24-case paired matrix is required')
    run = inside(root, manifest['run_root'])
    for actual, case in zip(manifest['cases'], expected, strict=True):
        if any(actual.get(key) != value for key, value in case.items()):
            raise ValueError('Paired condition, arm, order or parent configuration changed')
        selected = target.parent / 'plans' / (case['case_id'] + '.json')
        raw = encoded(case_plan(case, manifest['study_source_id'], manifest['git_base']))
        directory = run / 'trials' / case['case_id']
        paths = {'plan_path': selected, 'epoch_directory': directory, 'recovery_path': directory/'recovery.json',
            'warmup_path': directory/'warmup.json', 'result_directory': directory/'real-model',
            'result_path': directory/'real-model'/(case['case_id']+'.json')}
        if any(inside(root, actual[key]) != value for key, value in paths.items()) or actual['plan_sha256'] != digest(raw) or selected.read_bytes() != raw:
            raise ValueError('Selected plan or exclusive result path changed')
    return manifest


def primary_metrics(document):
    """Count timely correct responses against all frozen measurement arrivals."""
    from collections import Counter
    from v2.workload import correct
    config, result = document['configuration'], document['result']
    if config['measurement_seconds'] != 335 or config['deadline_seconds'] != 10:
        raise ValueError('The fixed arrival window or client deadline changed')
    trace = {row['id']: row for row in config['trace']}
    if len(trace) != len(config['trace']) or Counter(row['id'] for row in result['requests']) != Counter(trace.keys()):
        raise ValueError('Every scheduled arrival needs exactly one client outcome')
    measured = []
    for row in result['requests']:
        item = trace[row['id']]
        if (type(row.get('status')) is not int or type(row.get('correct')) is not bool
                or type(row.get('latency_seconds')) not in (int, float)
                or not math.isfinite(row['latency_seconds']) or row['latency_seconds'] < 0):
            raise ValueError('Client outcome fields are invalid')
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (row['scheduled'], row['finished'], result['start'])):
            raise ValueError('Client clock values are invalid')
        if (abs(row['scheduled'] - result['start'] - item['offset']) > 1e-7
                or abs(row['finished'] - row['scheduled'] - row['latency_seconds']) > 1e-7):
            raise ValueError('Client schedule or latency cannot be replayed')
        if row['status'] == 200:
            body = row['response']; computed = correct(body['inference'], item)
            if row['correct'] != computed or body.get('correct') != computed:
                raise ValueError('Client correctness is not supported by the archived model response')
        elif row['correct']:
            raise ValueError('An unsuccessful HTTP result cannot be correct')
        if config['warmup_seconds'] <= item['offset'] < config['warmup_seconds'] + 335:
            measured.append(row)
    if not measured:
        raise ValueError('The measurement arrival cohort is empty')
    useful = sum(row['status'] == 200 and row['correct'] and row['latency_seconds'] <= 10 for row in measured)
    statuses = Counter(str(row['status']) for row in measured)
    return {'measurement_arrivals': len(measured), 'timely_correct_arrivals': useful,
            'timely_correct_fraction': useful / len(measured) if measured else None,
            'timely_correct_per_second': useful / 335, 'http429_arrivals': statuses['429'],
            'client_status_counts': dict(sorted(statuses.items()))}


def paired_summary(rows):
    """Expose every pair; incomplete/invalid pairs cannot silently disappear."""
    groups = {}
    for row in rows:
        groups.setdefault(row['pair_id'], {})[row['mode']] = row
    pairs = []
    for pair_id, arms in sorted(groups.items()):
        complete = (set(arms) == {'bypass', 'global'}
                    and all(row.get('primary_observable') is True for row in arms.values()))
        pair = {'pair_id': pair_id, 'complete': complete,
                'bypass_case': arms.get('bypass', {}).get('case_id'),
                'global_case': arms.get('global', {}).get('case_id'),
                'global_minus_bypass_fraction': None, 'global_minus_bypass_per_second': None}
        if complete:
            bypass, global_arm = arms['bypass'], arms['global']
            if bypass['measurement_arrivals'] != global_arm['measurement_arrivals']:
                raise ValueError('Paired arms do not have the same full arrival denominator')
            pair['global_minus_bypass_fraction'] = global_arm['timely_correct_fraction'] - bypass['timely_correct_fraction']
            pair['global_minus_bypass_per_second'] = global_arm['timely_correct_per_second'] - bypass['timely_correct_per_second']
        pairs.append(pair)
    return pairs


def utc_time(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        raise ValueError('Recorded study timestamps must identify UTC')
    return stamp


def recovery_module(root):
    import importlib.util
    spec = importlib.util.spec_from_file_location('alien6_admission_recovery_report', inside(root, 'infra/recover-fra6s004-admission.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def control_binding(root, manifest, network, collection):
    """Bind base collectors to the new instrument and the same fresh empty epoch."""
    run = inside(root, manifest['run_root'])
    binding = json.loads((run/'study-control-binding.json').read_bytes())
    if (binding.get('schema') != 'alien6-admission-control-binding-v1'
            or binding.get('source_id') != manifest['study_source_id']
            or binding.get('base_source_id') != BASE_SOURCE_ID or binding.get('git_base') != manifest['git_base']
            or binding.get('manifest_sha256') != digest(encoded(manifest))):
        raise ValueError('Fresh controls are not bound to this exact study manifest')
    paths = [run/'network/control.json', run/'initial-recovery.json', *sorted((run/'qualification').glob('*.json'))]
    expected = {row['path']: row for row in (file_record(root, path) for path in paths)}
    recorded = binding.get('files')
    if (len(expected) != 16 or not isinstance(recorded, list) or len(recorded) != 16
            or {row['path']: row for row in recorded} != expected):
        raise ValueError('Control binding does not preserve all sixteen exact evidence files')
    recovery = json.loads((run/'initial-recovery.json').read_bytes())
    proof = recovery_module(root).verify_admission_recovery_record(recovery, manifest['study_source_id'], manifest['git_base'])
    if proof.get('valid') is not True or binding.get('after_controls_admission_state') != recovery.get('post_admission_state'):
        raise ValueError('The new proxy was not the same clean empty epoch throughout no-inference controls')
    times = [manifest['created_utc'], recovery['started_utc'], recovery['finished_utc'],
             network['utc'], collection['utc'], binding['created_utc']]
    stamps = [utc_time(value) for value in times]
    if stamps != sorted(stamps):
        raise ValueError('Fresh controls do not precede comparisons in the recorded UTC ordering')
    return {'path': str((run/'study-control-binding.json').relative_to(root)),
            'sha256': digest((run/'study-control-binding.json').read_bytes()), 'created_utc': binding['created_utc']}


def refresh_controls(root, manifest):
    from v2.verify import replay_network_control, replay_continuation_trial_gate, verify_cpu_metadata
    try:
        run = inside(root, manifest['run_root'])
        network = json.loads((run/'network/control.json').read_bytes())
        if (network.get('source_id') != BASE_SOURCE_ID or network.get('git_base') != manifest['git_base']
                or network.get('plan_sha256') != manifest['parent_plan']['sha256']
                or len(network['trials']) != 3 or network.get('qualified') is not True
                or not replay_network_control(network)):
            raise ValueError('Fresh transport controls did not reproduce')
        parent = json.loads(inside(root, manifest['parent_plan']['path']).read_bytes())
        directory = run/'qualification'
        block = json.loads((directory/'block.json').read_bytes())
        collection = json.loads((directory/'manifest.json').read_bytes())
        if (block != {'complete': True, 'qualified': True, 'trials': 12, 'invalid': []}
                or collection.get('source_id') != BASE_SOURCE_ID or collection.get('git_base') != manifest['git_base']
                or collection.get('plan_sha256') != manifest['parent_plan']['sha256']):
            raise ValueError('Fresh generator qualification is incomplete')
        verify_cpu_metadata(collection['metadata'], 'v2-ollama-fra6s004-cpu-long-window')
        base = verify_registered(root, BASE_SOURCE_ID)
        expected = {cfg['trial_id']: cfg for cfg in parent['blocks']['model-qualification']}
        paths = list(directory.glob('model-qualification-*.json'))
        if len(paths) != len(expected) or len(expected) != 12:
            raise ValueError('All twelve fresh generator conditions are required')
        seen = set()
        for target in paths:
            document = json.loads(target.read_bytes()); config = document['configuration']
            actual = {key: value for key, value in config.items() if key not in ('trace', 'trace_decoded_sha256', 'worker_addresses')}
            if actual != expected[config['trial_id']] or config['trial_id'] in seen or document.get('valid') is not True:
                raise ValueError('Fresh generator condition changed')
            if replay_continuation_trial_gate(document, BASE_SOURCE_ID,
                    base['files']['v2/model_service.py']['sha256'], manifest['git_base']):
                raise ValueError('Fresh generator qualification did not replay')
            seen.add(config['trial_id'])
        binding = control_binding(root, manifest, network, collection)
        if collection.get('node_limits') != {'nano_cpus': 4_000_000_000, 'memory_bytes': 8 * 1024**3}:
            raise ValueError('Generator qualification used different node limits')
        return {'qualified': True, 'issues': [], 'binding': binding, 'image_id': collection['image_id']}
    except (OSError, KeyError, ValueError, TypeError, AssertionError, RuntimeError) as error:
        return {'qualified': False, 'issues': ['fresh_controls_unavailable:' + type(error).__name__]}


def epoch_issues(root, case, document, study, base):
    from v2.verify import verify_standalone_warmup, verify_cpu_envelope
    try:
        module = recovery_module(root)
        recovery = json.loads(inside(root, case['recovery_path']).read_bytes())
        proof = module.verify_admission_recovery_record(recovery, study['source_id'], study['git_base'])
        if proof.get('valid') is not True:
            raise ValueError('Native process reset is not certified')
        warmup = json.loads(inside(root, case['warmup_path']).read_bytes())
        if (warmup.get('source_id') != base['source_id'] or warmup.get('git_base') != base['git_base']
                or warmup.get('valid') is not True or verify_standalone_warmup(warmup) != 4
                or any(row['raw'].get('done') is not True for row in warmup['observations'])):
            raise ValueError('The common four-response warmup did not reproduce')
        before = document['admission_certificate']['pre_native_state']
        if recovery.get('new_epoch') != before['clock_domain']:
            raise ValueError('Recovery and measured proxy epochs differ')
        retained = sorted(before['records'], key=lambda row: row['sequence'])
        if len(retained) != 4 or any(row['status'] != observation['status'] or row['response'] != observation['raw']
                for row, observation in zip(retained, warmup['observations'], strict=True)):
            raise ValueError('Warmup answers differ from the retained native ledger')
        for sample in document['native_observations']:
            if not sample.get('linux_cgroups'):
                raise ValueError('Native resource envelope is unavailable')
            for group in sample['linux_cgroups'].values():
                verify_cpu_envelope(group, 'v2-ollama-fra6s004-cpu-long-window')
        return []
    except (OSError, KeyError, ValueError, TypeError, AssertionError, RuntimeError) as error:
        return ['epoch_preconditions_unavailable:' + type(error).__name__]


def verify_case_collection(collection, case, manifest, snapshot, controls):
    """Bind each result manifest to its selected plan, instrument, image and node."""
    expected = {'schema': 'alien6-admission-case-v1',
        'source_id': manifest['study_source_id'], 'base_source_id': BASE_SOURCE_ID,
        'git_base': manifest['git_base'], 'plan_sha256': case['plan_sha256'],
        'driver_sha256': snapshot['files']['v2/admission_driver.py']['sha256'],
        'admission_proxy_sha256': snapshot['files']['v2/admission_proxy.py']['sha256'],
        'model_controller_sha256': snapshot['files']['v2/model_service.py']['sha256'],
        'node_limits': {'nano_cpus': 4_000_000_000, 'memory_bytes': 8 * 1024**3}}
    if any(collection.get(key) != value for key, value in expected.items()):
        raise ValueError('Case collection manifest differs from the frozen source, plan or node limits')
    image = controls.get('image_id')
    if not isinstance(image, str) or not image.startswith('sha256:') or len(image) != 71 or collection.get('image_id') != image:
        raise ValueError('Case image differs from the image used for qualification')
    if utc_time(collection['utc']) < utc_time(controls['binding']['created_utc']):
        raise ValueError('Comparison collection predates the fresh control binding')


def analyze_study(path, *, root=None):
    """Recompute the new matrix only; no pooling with the exploratory parent45."""
    from v2.admission_certificate import replay_certificate
    from v2.verify import replay_continuation_trial_gate
    root = Path(root or ROOT).resolve()
    manifest = check_study(path, root=root)
    snapshot = verify_registered(root, manifest['study_source_id'])
    study = {'source_id': manifest['study_source_id'], 'git_base': manifest['git_base'],
             'admission_proxy_sha256': snapshot['files']['v2/admission_proxy.py']['sha256']}
    base = {'source_id': BASE_SOURCE_ID, 'git_base': manifest['git_base'],
            'proxy_sha256': snapshot['files']['v2/inference_proxy.py']['sha256']}
    controls = refresh_controls(root, manifest)
    rows = []
    for case in manifest['cases']:
        row = {key: case[key] for key in ('case_id', 'pair_id', 'mode', 'capacity', 'parent_trial_id')}
        row.update({key: case['configuration'][key] for key in ('replicas', 'profile', 'repetition')})
        row.update(status='pending', primary_observable=False, resource_qualified=False,
                   execution_issues=[], native_issues=[], counts={})
        target = inside(root, case['result_path'])
        if target.exists():
            raw = target.read_bytes(); document = json.loads(raw)
            row.update(source_path=case['result_path'], source_sha256=digest(raw))
            try:
                actual = {key: value for key, value in document['configuration'].items()
                          if key not in ('trace', 'trace_decoded_sha256', 'worker_addresses', 'shared_admission')}
                if actual != case['configuration']:
                    raise ValueError('Frozen paired configuration changed')
                expected_context = case_plan(case, manifest['study_source_id'], manifest['git_base'])['shared_admission']
                if document['configuration'].get('shared_admission') != expected_context:
                    raise ValueError('Configured admission arm differs from its frozen plan')
                if document.get('study_identity') != {'source_id': study['source_id'], 'git_base': study['git_base']} or document.get('base_identity') != {'source_id': base['source_id'], 'git_base': base['git_base']}:
                    raise ValueError('Observation collection identities differ')
                verdict = replay_certificate(document['configuration'], document['result'], document.get('admission_certificate'), study, base)
                gates = replay_continuation_trial_gate(document, base['source_id'],
                            snapshot['files']['v2/model_service.py']['sha256'], base['git_base'])
                row.update(primary_metrics(document), **verdict)
                if controls['qualified']:
                    collection = json.loads((target.parent/'manifest.json').read_bytes())
                    verify_case_collection(collection, case, manifest, snapshot, controls)
                row['execution_issues'] += gates + controls['issues'] + epoch_issues(root, case, document, study, base)
                reference = document['configuration']['trace_reference']
                expected_trace = json.loads(gzip.decompress(inside(root, reference['path']).read_bytes()))
                if document['configuration']['trace'] != expected_trace or document['configuration']['trace_decoded_sha256'] != digest(canonical(expected_trace)):
                    raise ValueError('Decoded frozen workload changed')
                if document.get('collection_error'):
                    row['execution_issues'].append('controller_collection_failed')
                row['primary_observable'] = verdict['coverage_valid'] and not row['execution_issues']
                row['resource_qualified'] = row['primary_observable'] and verdict['native_complete']
                if document.get('capture_valid') != row['primary_observable'] or document.get('resource_qualified') != row['resource_qualified']:
                    row['execution_issues'].append('archived_validity_claim_differs')
                    row['primary_observable'] = row['resource_qualified'] = False
                row['status'] = 'execution_defect' if row['execution_issues'] else 'resource_censored' if not row['resource_qualified'] else 'observed'
            except (KeyError, TypeError, ValueError, AssertionError) as error:
                row['execution_issues'].append(str(error)); row['status'] = 'execution_defect'
        rows.append(row)
    pairs = paired_summary(rows)
    return {'schema': 'alien6-shared-admission-analysis-v1', 'study_source_id': manifest['study_source_id'],
        'manifest': file_record(root, path), 'complete': all(row['status'] != 'pending' for row in rows),
        'all_primary_observable': all(row['primary_observable'] for row in rows),
        'confirmatory_inference': False, 'parent_observations_pooled': False, 'controls': controls,
        'trials': rows, 'pairs': pairs,
        'interpretation': 'Twelve paired conditions, three trace repetitions per replica/load group. Differences are exploratory; no request pooling, survivor-only resource comparison, powered interval or library-wide conclusion.'}


def write_analysis(path, out, *, root=None):
    report = analyze_study(path, root=root)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=False)
    (target/'results.json').write_bytes(encoded(report))
    fields = ('case_id', 'pair_id', 'replicas', 'profile', 'repetition', 'mode', 'capacity', 'status', 'primary_observable', 'resource_qualified',
              'measurement_arrivals', 'timely_correct_arrivals', 'timely_correct_fraction',
              'timely_correct_per_second', 'http429_arrivals', 'execution_issues', 'native_issues', 'counts')
    with (target/'trials.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for row in report['trials']:
            writer.writerow({key: json.dumps(row[key], sort_keys=True) if isinstance(row.get(key), (list, dict)) else row.get(key) for key in fields})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare'); prepare.add_argument('--parent-manifest', type=Path, required=True)
    prepare.add_argument('--out', type=Path, required=True); prepare.add_argument('--study-source-id', required=True)
    prepare.add_argument('--run-root', default='runs/shared-admission')
    check = sub.add_parser('check'); check.add_argument('--manifest', type=Path, required=True); check.add_argument('--live', action='store_true')
    analysis = sub.add_parser('report', aliases=['analyze']); analysis.add_argument('--manifest', type=Path, required=True); analysis.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare_study(args.parent_manifest, args.out, study_source_id=args.study_source_id, run_root=args.run_root)
        print(json.dumps({'cases': len(result['cases']), 'capacity': result['calibration']['selection']['capacity']}))
    elif args.command == 'check':
        result = check_study(args.manifest, live=args.live); print(json.dumps({'valid': True, 'cases': len(result['cases'])}))
    else:
        result = write_analysis(args.manifest, args.out); print(json.dumps({'complete': result['complete'], 'cases': len(result['trials'])}))
        if any(row['status'] == 'execution_defect' for row in result['trials']):
            raise SystemExit(1)


if __name__ == '__main__': main()
