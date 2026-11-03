"""Describe every planned CPU observation without qualifying a repaired aggregate."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v2.analyze import summarize
from v2.continuation import check_manifest, inside
from v2.identity import verify_registered
from v2.model import expected_native_calls, validate_proxy_state
from v2.plan import canonical
from v2.verify import (replay_continuation_trial_gate, replay_native_completion_certificate,
                       replay_network_control, verify_cpu_envelope, verify_standalone_warmup,
                       verify_direct_model_answers, verify_source_metadata, verify_cpu_metadata)
from v2.workload import correct

RESOURCE_ISSUES = frozenset(('code_or_resource_telemetry_missing', 'resource_telemetry_missing',
    'observed_oom_kill', 'kubernetes_or_host_telemetry_missing', 'pod_membership_changed_during_trial',
    'pod_restart', 'sampled_worker_uid_set_differs', 'inference_process_observations_missing',
    'other_ollama_server_not_observed_idle', 'cpu_envelope_not_reproduced'))
CSV_FIELDS = ('parent_index', 'trial_id', 'segment', 'epoch', 'profile', 'path', 'replicas', 'repetition',
    'observation_status', 'primary_scope', 'resource_qualified', 'native_completion', 'bba_valid',
    'measurement_arrivals', 'timely_correct_arrivals', 'timely_correct_fraction',
    'timely_correct_per_arrival_second', 'native_expected', 'native_completed', 'native_uncertain',
    'native_active', 'execution_defects', 'resource_issues', 'bba_gate_issues', 'source_path', 'source_sha256')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(root, path):
    return json.loads(inside(root, path).read_bytes())


def primary_metrics(document):
    """Independently use all scheduled measurement arrivals as the denominator."""
    config, result = document['configuration'], document['result']
    if config['deadline_seconds'] != 10 or config['measurement_seconds'] != 335:
        raise ValueError('The unchanged ten-second deadline and 335-second window are required')
    trace = {row['id']: row for row in config['trace']}
    outcomes = result['requests']
    if len(trace) != len(config['trace']) or Counter(row['id'] for row in outcomes) != Counter(trace.keys()):
        raise ValueError('The full arrival cohort must have exactly one client outcome per planned request')
    first, duration = config['warmup_seconds'], config['measurement_seconds']
    measured = []
    for row in outcomes:
        if (type(row.get('status')) is not int or not isinstance(row.get('correct'), bool)
                or type(row.get('latency_seconds')) not in (int, float)
                or not math.isfinite(row['latency_seconds']) or row['latency_seconds'] < 0):
            raise ValueError('A client outcome has incomplete status, correctness or latency')
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (result['start'], row['scheduled'], row['finished'])):
            raise ValueError('A client outcome has no finite local clock interval')
        if abs(row['scheduled'] - result['start'] - trace[row['id']]['offset']) >= 1e-7:
            raise ValueError('A client schedule differs from its frozen arrival')
        if abs(row['finished'] - row['scheduled'] - row['latency_seconds']) >= 1e-9:
            raise ValueError('A client latency cannot be replayed')
        if config.get('direct_ollama'):
            verify_direct_model_answers(row, trace[row['id']])
        elif row['status'] == 200:
            body = row['response']
            expected = correct(body['inference'], trace[row['id']])
            if row['correct'] != expected or body.get('correct') != expected:
                raise ValueError('A timely worker correctness flag differs from the native response')
        if row['status'] != 200 and row['correct']:
            raise ValueError('A failed HTTP outcome cannot count as a correct client response')
        if first <= trace[row['id']]['offset'] < first + duration:
            measured.append(row)
    useful = sum(row['status'] == 200 and row['correct'] and row['latency_seconds'] <= 10 for row in measured)
    return {'measurement_arrivals': len(measured), 'timely_correct_arrivals': useful,
            'timely_correct_fraction': useful / len(measured) if measured else None,
            'timely_correct_per_arrival_second': useful / duration,
            'offered_per_second': len(measured) / duration,
            'client_status_counts': dict(sorted(Counter(str(row['status']) for row in measured).items())),
            'client_failure_reasons': dict(sorted(Counter(row.get('reason') or row.get('response', {}).get('reason')
                    or 'unspecified' for row in measured if not (row['status'] == 200 and row['correct'] and row['latency_seconds'] <= 10)).items()))}


def classify_document(document, collection, frozen, identity, *, phase_issues=()):
    """Separate observable client outcomes, resources and native completion."""
    defects, resource, gates = list(phase_issues), [], []
    actual = {key: value for key, value in document['configuration'].items()
              if key not in ('trace', 'trace_decoded_sha256', 'worker_addresses')}
    if actual != frozen:
        defects.append('frozen_configuration_changed')
    try:
        gates = replay_continuation_trial_gate(document, identity['source_id'],
                    identity.get('model_controller_sha256', collection['model_controller_sha256']), identity['git_base'])
    except (KeyError, TypeError, ValueError, AssertionError) as error:
        defects.append('instrument_gate_unavailable:' + type(error).__name__)
    resource.extend(issue for issue in gates if issue in RESOURCE_ISSUES)
    defects.extend(issue for issue in gates if issue not in RESOURCE_ISSUES)
    try:
        verify_cpu_metadata(collection['metadata'], 'v2-ollama-fra6s004-cpu-long-window')
        samples = document['native_observations']
        if not samples or any(not row.get('linux_cgroups') for row in samples):
            raise ValueError('Missing native cgroup observations')
        for sample in samples:
            for group in sample['linux_cgroups'].values():
                verify_cpu_envelope(group, 'v2-ollama-fra6s004-cpu-long-window')
    except (KeyError, TypeError, ValueError):
        resource.append('cpu_envelope_not_reproduced')
    metrics, summary_match = {}, None
    try:
        metrics = primary_metrics(document)
        if not metrics['measurement_arrivals']:
            defects.append('empty_measurement_arrival_cohort')
    except (KeyError, TypeError, ValueError, AssertionError) as error:
        defects.append('client_cohort_unavailable:' + type(error).__name__)
    if metrics:
        try:
            existing = summarize(document)
            pairs = {'offered': 'measurement_arrivals', 'useful': 'timely_correct_arrivals',
                     'useful_fraction': 'timely_correct_fraction', 'useful_cohort_per_second': 'timely_correct_per_arrival_second'}
            if any(existing[a] != metrics[b] for a, b in pairs.items()):
                raise ValueError('Existing and independent client metrics differ')
            summary_match = True
        except (KeyError, TypeError, ValueError, AssertionError) as error:
            summary_match = False
            defects.append('existing_metric_replay_unavailable:' + type(error).__name__)
    certificate = document.get('native_completion_certificate')
    native, certified = 'uncertified', False
    try:
        certified = replay_native_completion_certificate(document['configuration'], document['result'], certificate, identity)
        if certified:
            native = 'certified'
    except (KeyError, TypeError, ValueError, AssertionError) as error:
        native = 'invalid_claim'
        defects.append('native_certificate_claim_not_reproduced:' + type(error).__name__)
    native_counts = {'native_expected': None, 'native_completed': None, 'native_uncertain': None, 'native_active': None}
    if isinstance(certificate, dict) and certified:
        try:
            expected = expected_native_calls(document['configuration'], document['result'])
            if expected != certificate.get('expected'):
                raise ValueError('Expected native coverage changed')
            before, terminal = certificate['pre_state'], certificate['terminal_state']
            validate_proxy_state(before, identity)
            validate_proxy_state(terminal, identity, before)
            native_counts.update(native_expected=expected['count'],
                native_completed=terminal['finished_total'] - before['finished_total'],
                native_uncertain=terminal['uncertain_total'] - before['uncertain_total'], native_active=len(terminal['active']))
        except (KeyError, TypeError, ValueError):
            pass  # Keep unavailable evidence unknown, including for failed trials.
    if not certified and not gates:
        try:
            accounted = account_incomplete_native(document, identity,
                identity.get('model_controller_sha256', collection['model_controller_sha256']))
            if accounted.get('classification') != 'native_completion_incomplete' or accounted.get('strict_trial_valid') is not False:
                raise ValueError('An incomplete observation was promoted or not accounted for')
            native = 'uncertain'
            native_counts.update(native_expected=accounted['expected_native_calls'],
                native_completed=accounted['terminal_native_calls'], native_uncertain=accounted['uncertain_terminal_calls'],
                native_active=accounted['active_at_capture'])
        except (OSError, KeyError, TypeError, ValueError, AssertionError) as error:
            defects.append('native_attempt_accounting_unavailable:' + type(error).__name__)
    envelope_qualified = not resource
    if not certified:
        resource.append('native_resource_lifetime_censored')
    replayed_valid = not gates and certified and not any(issue.startswith('instrument_gate_unavailable') for issue in defects)
    if document.get('valid') != replayed_valid:
        defects.append('archived_validity_claim_not_reproduced')
    status = 'execution_defect' if defects else 'native_completion_uncertain' if not certified else 'resource_gate_failed' if resource else 'certified_observation'
    return {**metrics, **native_counts, 'bba_valid': replayed_valid, 'archived_valid': document.get('valid'),
            'primary_scope': 'complete_arrival_cohort' if metrics and metrics['measurement_arrivals'] else 'unavailable',
            'resource_envelope_qualified': envelope_qualified, 'resource_qualified': certified and not resource,
            'native_completion': native, 'observation_status': status,
            'execution_defects': sorted(set(defects)), 'resource_issues': sorted(set(resource)),
            'bba_gate_issues': sorted(set(gates)), 'existing_metric_replay_matches': summary_match,
            'epoch': (certificate or {}).get('pre_state', {}).get('clock_domain')}


def recovery_validator(root):
    path = inside(root, 'infra/recover-fra6s004-inference.py')
    spec = importlib.util.spec_from_file_location('alien6_recovery_report', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.verify_recovery_record


def account_incomplete_native(document, identity, controller_hash):
    """Use the supervisor's strict, pure controller/payload/ledger accounting."""
    path = ROOT / 'infra/run-fra6s004-continuation.py'
    spec = importlib.util.spec_from_file_location('alien6_continuation_report', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.classify_trial(document, identity, controller_hash)


def epoch_issues(root, entry, document, identity):
    issues = []
    try:
        recovery = read(root, entry['recovery_path'])
        proof = recovery_validator(root)(recovery, identity['source_id'], identity['git_base'])
        if not isinstance(proof, dict) or proof != {'valid': True, 'new_epoch': recovery.get('new_epoch'),
                'source_id': identity['source_id'], 'generation_requests': 0}:
            raise ValueError('Recovery did not qualify')
        warmup = read(root, entry['warmup_path'])
        if (warmup.get('source_id') != identity['source_id'] or warmup.get('git_base') != identity['git_base']
                or verify_standalone_warmup(warmup) != 4 or warmup.get('valid') is not True
                or any(row['raw'].get('done') is not True for row in warmup['observations'])):
            raise ValueError('The common four-response warmup did not qualify')
        before = document['native_completion_certificate']['pre_state']
        if (recovery['new_epoch'] != before['clock_domain'] or before['started_total'] != 4
                or before['finished_total'] != 4 or before['active'] or before['uncertain_total']):
            raise ValueError('Recovery, warmup and observed proxy epoch differ')
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, AssertionError) as error:
        issues.append('epoch_preconditions_unavailable:' + type(error).__name__)
    return issues


def refresh_controls(root, manifest):
    try:
        network = read(root, manifest['network_path'])
        if len(network['trials']) != 3 or network.get('qualified') is not True or not replay_network_control(network):
            raise ValueError('Transport controls did not qualify')
        directory = inside(root, manifest['qualification_directory'])
        block = json.loads((directory / 'block.json').read_bytes())
        if (block.get('complete') is not True or block.get('qualified') is not True
                or block.get('trials') != 12 or block.get('invalid') != []):
            raise ValueError('The complete qualification did not pass')
        collection = json.loads((directory / 'manifest.json').read_bytes())
        trials = [json.loads(path.read_bytes()) for path in sorted(directory.glob('model-qualification-*.json'))]
        if len(trials) != 12 or len({row['configuration']['trial_id'] for row in trials}) != 12:
            raise ValueError('The qualification grid is incomplete')
        for trial in trials:
            if replay_continuation_trial_gate(trial, manifest['source_id'], collection['model_controller_sha256'], manifest['git_base']) or not trial.get('valid'):
                raise ValueError('A qualification trial does not reproduce')
        return {'qualified': True, 'issues': []}
    except (OSError, KeyError, TypeError, ValueError, AssertionError) as error:
        return {'qualified': False, 'issues': ['refresh_controls_unavailable:' + type(error).__name__]}


def build_report(manifest_path, *, root=None):
    root = Path(root or ROOT).resolve()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('Use Python 3.12 for the pinned BBA replay; the patch version is recorded')
    manifest = check_manifest(manifest_path, root=root)
    parent = read(root, manifest['parent_plan']['path'])
    prior = inside(root, manifest['prior']['directory'])
    original_collection = json.loads((prior / 'manifest.json').read_bytes())
    registered = verify_registered(root, manifest['source_id'])
    identity = {'source_id': manifest['source_id'], 'git_base': manifest['git_base'],
                'proxy_sha256': registered['files']['v2/inference_proxy.py']['sha256'],
                'model_controller_sha256': registered['files']['v2/model_service.py']['sha256']}
    instruments = json.loads((root / 'instruments/manifest.json').read_bytes())['sources']
    controls = refresh_controls(root, manifest)
    suffix = {row['parent_index']: row for row in manifest['trials']}
    rows, seen_epochs = [], set()
    for index, frozen in enumerate(parent['blocks']['real-model']):
        entry = suffix.get(index)
        path = inside(root, entry['result_path']) if entry else prior / (frozen['trial_id'] + '.json')
        row = {'parent_index': index, 'trial_id': frozen['trial_id'], 'segment': 'continuation_reset_epoch' if entry else 'original_shared_epoch',
               'profile': frozen['profile'], 'path': 'direct-model' if frozen.get('direct_ollama') else 'workers',
               'replicas': frozen['replicas'], 'repetition': frozen['repetition'], 'epoch': None,
               'observation_status': 'not_observed', 'primary_scope': 'not_observed',
               'resource_qualified': None, 'native_completion': 'not_observed', 'bba_valid': None,
               'execution_defects': [], 'resource_issues': [], 'bba_gate_issues': [],
               'source_path': path.relative_to(root).as_posix(), 'source_sha256': None}
        if path.exists():
            raw = path.read_bytes(); document = json.loads(raw)
            encoded = inside(root, frozen['trace_reference']['path']).read_bytes()
            if (digest(encoded) != frozen['trace_reference']['sha256']
                    or document['configuration'].get('trace') != json.loads(gzip.decompress(encoded))
                    or document['configuration'].get('trace_decoded_sha256') != digest(canonical(document['configuration']['trace']))):
                raise ValueError('A result differs from its immutable parent workload: ' + frozen['trial_id'])
            collection = read(root, str(Path(entry['result_directory']) / 'manifest.json')) if entry else original_collection
            expected_plan = entry['plan_sha256'] if entry else manifest['parent_plan']['sha256']
            phase = list(controls['issues']) if entry else []
            if (collection.get('source_id') != identity['source_id'] or collection.get('git_base') != identity['git_base']
                    or collection.get('plan_sha256') != expected_plan
                    or collection.get('model_controller_sha256') != identity['model_controller_sha256']):
                phase.append('collection_manifest_identity_changed')
            try:
                verify_source_metadata(collection, instruments)
            except (KeyError, TypeError, ValueError, AssertionError):
                phase.append('collection_source_provenance_not_reproduced')
            if entry:
                phase.extend(epoch_issues(root, entry, document, identity))
            row.update(classify_document(document, collection, frozen, identity, phase_issues=phase))
            row['source_sha256'] = digest(raw)
            if entry and row['epoch']:
                if row['epoch'] in seen_epochs:
                    row['execution_defects'].append('proxy_epoch_reused')
                    row['observation_status'] = 'execution_defect'
                seen_epochs.add(row['epoch'])
            elif row['epoch']:
                seen_epochs.add(row['epoch'])
        rows.append(row)
    observed = [row for row in rows if row['source_sha256']]
    suffix_rows = rows[17:]
    complete = (len(rows) == 45 and len(observed) == 45 and all(row['primary_scope'] == 'complete_arrival_cohort'
                and not row['execution_defects'] for row in suffix_rows) and controls['qualified'])
    return {'schema': 'alien6-cpu-continuation-report-v1', 'python': sys.version.split()[0],
        'source_id': manifest['source_id'], 'git_base': manifest['git_base'],
        'manifest': {'path': inside(root, manifest_path, cli=True).relative_to(root).as_posix(),
                     'sha256': digest(inside(root, manifest_path, cli=True).read_bytes())},
        'planned_trials': 45, 'observed_trials': len(observed), 'remaining_observations': 45 - len(observed),
        'continuation_observed': sum(row['source_sha256'] is not None for row in suffix_rows),
        'exploratory_observations_complete': complete, 'parent_block_qualified': False,
        'qualified': False, 'included_for_inference': False, 'refresh_controls': controls,
        'status_counts': dict(sorted(Counter(row['observation_status'] for row in rows).items())), 'trials': rows,
        'interpretation': 'Every planned condition remains visible. Client outcomes use the full scheduled arrival cohort. Native uncertainty remains separate; late native responses never replace client scores. Epochs are not pooled for causal comparisons, confidence intervals or significance tests.'}


def write_report(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'results.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    with (output / 'trials.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator='\n')
        writer.writeheader()
        for row in report['trials']:
            writer.writerow({key: json.dumps(row.get(key), sort_keys=True, separators=(',', ':'))
                if isinstance(row.get(key), (dict, list)) else row.get(key) for key in CSV_FIELDS})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path, help='Fresh output directory')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('Output directory already exists; refusing to overwrite')
    report = build_report(args.manifest)
    write_report(report, args.out)
    print(json.dumps({key: report[key] for key in ('observed_trials', 'remaining_observations', 'qualified', 'included_for_inference')}))


if __name__ == '__main__':
    main()
