"""Collect one prospective admission arm without modifying the BBA instruments."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

from v2.identity import capture, verify_worktree
from v2 import model
from v2.plan import ROOT, canonical
from v2.run import CONTROL, Observer, command, json_http, kubectl, scale
from v2.service import code_hash
from v2.verify import replay_continuation_trial_gate
from v2.workload import MODEL, DIGEST


def study_identity(root=ROOT):
    from v2.admission_study import BASE_SOURCE_ID, STUDY_FILES
    base, study = capture(root), capture(root, files=STUDY_FILES)
    if base.source_id != BASE_SOURCE_ID:
        raise ValueError('The byte-preserved base instrument changed')
    verify_worktree(root, base.source_id); verify_worktree(root, study.source_id)
    return base, study


def preflight(configuration, study, base):
    url = configuration['provider']['ollama_url'].rstrip('/')
    native = json_http(url + '/study-state?after=0&records=1', timeout=5)
    admission = json_http(url + '/study-admission/state?after=0&records=1', timeout=5)
    identity = {**base.metadata(), 'proxy_sha256': hashlib.sha256((ROOT/'v2/inference_proxy.py').read_bytes()).hexdigest()}
    model.validate_proxy_state(native, identity)
    expected = Counter(model.model_request_sha256(model.pilot_item(index)) for index in range(4))
    if (native['active'] or native['uncertain_total'] or native['started_total'] != 4 or native['finished_total'] != 4
            or Counter(row['request_sha256'] for row in native.get('records', [])) != expected
            or any(row.get('status') != 200 or row.get('done') is not True or row.get('uncertain') is not False
                   for row in native.get('records', []))):
        raise ValueError('The native epoch must contain exactly four successful technical warmups')
    if (admission.get('source_id') != study.source_id or admission.get('git_base') != study.git_base
            or admission.get('base_source_id') != base.source_id or admission.get('clock_domain') != native['clock_domain']
            or admission.get('admission_proxy_sha256') != hashlib.sha256((ROOT/'v2/admission_proxy.py').read_bytes()).hexdigest()
            or admission.get('configured') is not False or admission.get('configuration_record') is not None
            or admission.get('started_total') != 4 or admission.get('finished_total') != 4
            or admission.get('active') != [] or admission.get('occupied') != 0 or admission.get('quarantined') != 0
            or Counter(row.get('request_sha256') for row in admission.get('records', [])) != expected
            or any(row.get('decision') != 'admitted' or row.get('permit_released') is not True
                   or row.get('quarantined') is not False or row.get('trial_id') != 'technical-warmup'
                   for row in admission.get('records', []))):
        raise ValueError('The admission epoch does not match the common warmup')
    shared = configuration['shared_admission']
    configured = json_http(url + '/study-admission/configure', {'mode': shared['mode'],
        'capacity': shared['capacity'], 'trial_id': configuration['trial_id'],
        'study_source_id': study.source_id}, timeout=5)
    admission = json_http(url + '/study-admission/state?after=0&records=1', timeout=5)
    if admission.get('configuration_record') != configured or admission.get('mode') != shared['mode']:
        raise ValueError('The prospective arm was not configured exactly once')
    if admission.get('native_state') != native:
        raise ValueError('The idle native ledger changed while configuring the arm')
    return admission['native_state'], admission, identity


def collect_case(plan_path, out, image):
    from v2.admission_certificate import collect_certificate
    base, study = study_identity()
    plan = json.loads(plan_path.read_bytes())
    configs = plan['blocks']['real-model']
    if len(configs) != 1:
        raise ValueError('A separately isolated arm must contain exactly one condition')
    config = dict(configs[0])
    context = plan['shared_admission']
    if context['study_source_id'] != study.source_id or context['git_base'] != study.git_base:
        raise ValueError('Live collection source or actual Git base differs from the frozen plan')
    config['shared_admission'] = {**context, 'mode': context['mode'], 'capacity': context['capacity']}
    metadata = model.verify_server()
    command(['docker', 'update', '--cpus', '4', 'a6-capacity-v2-control-plane'])
    out.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 'alien6-admission-case-v1', 'utc': datetime.now(timezone.utc).isoformat(),
        **study.metadata(), 'base_source_id': base.source_id, 'code_sha256': code_hash(),
        'model_controller_sha256': hashlib.sha256((ROOT/'v2/model_service.py').read_bytes()).hexdigest(),
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'admission_proxy_sha256': hashlib.sha256((ROOT/'v2/admission_proxy.py').read_bytes()).hexdigest(),
        'metadata': metadata, 'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'kubernetes': json.loads(kubectl('version', '-o', 'json')),
        'kind_version': command(['kind', 'version']).strip(),
        'docker_version': command(['docker', 'version', '--format', '{{.Server.Version}}']).strip(),
        'image_id': command(['docker', 'image', 'inspect', image, '--format', '{{.Id}}']).strip(),
        'node_limits': json.loads(command(['docker', 'inspect', 'a6-capacity-v2-control-plane', '--format',
            '{"nano_cpus":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}']))}
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    before_native, before_admission, native_identity = preflight(config, study, base)
    routing = scale(config['replicas'])
    config['worker_addresses'] = ['http://' + pod['ip'] + ':8080' for pod in routing['pods']]
    trace = (ROOT/config['trace_reference']['path']).read_bytes()
    if hashlib.sha256(trace).hexdigest() != config['trace_reference']['sha256']:
        raise ValueError('Frozen paired workload changed')
    config['trace'] = json.loads(gzip.decompress(trace))
    config['trace_decoded_sha256'] = hashlib.sha256(canonical(config['trace'])).hexdigest()
    json_http(CONTROL + '/prepare', config)
    result, certificate, collection_error = None, None, None
    with Observer() as observer, model.InferenceObserver(out.name + ':' + config['trial_id']) as native:
        began = time.monotonic(); deadline = began + 900
        try:
            result = json_http(CONTROL + '/run', config, timeout=max(.001, deadline - time.monotonic()))
        except Exception as error:
            collection_error = {'type': type(error).__name__, 'detail': str(error)}
        certificate = collect_certificate(config, result, before_native, before_admission,
            {**study.metadata(), 'admission_proxy_sha256': manifest['admission_proxy_sha256']},
            native_identity, deadline, began)
    try:
        loaded = json_http(model.BASE + '/api/ps')
    except Exception as error:
        loaded = {'unavailable': type(error).__name__}
    document = {'schema': 'alien6-admission-observation-v1', 'configuration': config,
        'study_identity': study.metadata(), 'base_identity': base.metadata(),
        'routing': routing, 'observations': observer.samples, 'result': result,
        'native_observations': native.samples, 'inference_observer_metadata': native.metadata,
        'model_after_trial': loaded, 'admission_certificate': certificate,
        'collection_error': collection_error, 'execution_issues': [], 'native_issues': [],
        'capture_valid': False, 'resource_qualified': False}
    try:
        if result is None:
            raise ValueError('Controller result unavailable')
        issues = replay_continuation_trial_gate(document, base.source_id, manifest['model_controller_sha256'], base.git_base)
        if not result.get('drained'):
            issues.append('controller_roles_not_drained')
        issues += certificate.get('execution_issues', [])
        if not certificate.get('coverage_valid'):
            issues.append('admission_coverage_not_certified')
        document['execution_issues'] = sorted(set(issues))
        document['native_issues'] = certificate.get('native_issues', [])
        document['capture_valid'] = not issues
        document['resource_qualified'] = not issues and certificate.get('native_complete') is True
    except Exception as error:
        document['execution_issues'].append('capture_validation_' + type(error).__name__)
    target = out/(config['trial_id'] + '.json')
    with target.open('x') as stream:
        json.dump(document, stream, separators=(',', ':'), allow_nan=False); stream.write('\n')
    print(json.dumps({'trial_id': config['trial_id'], 'capture_valid': document['capture_valid'],
        'resource_qualified': document['resource_qualified'], 'execution_issues': document['execution_issues'],
        'native_issues': document['native_issues']}), flush=True)
    if not document['capture_valid']:
        raise RuntimeError('Captured arm failed technical integrity; preserve it without retry')
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--image', default='alien6-capacity:fra6s004-cpu-bba1acee')
    args = parser.parse_args()
    collect_case(args.plan, args.out, args.image)


if __name__ == '__main__':
    main()
