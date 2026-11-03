"""Check CPU study readiness without inference; observer sampling is opt-in."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import urllib.request
from urllib.error import HTTPError
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.cpu_campaigns import CPU_CAMPAIGNS, get_cpu_campaign
from v2.identity import capture, verify_registered, verify_worktree
from v2.model import hardware
from v2.plan import ROOT
from v2.verify import verify_cpu_envelope, verify_cpu_metadata
from v2.workload import DIGEST, MODEL, OPTIONS


def query(url, payload=None):
    raw = None if payload is None else json.dumps(payload, separators=(',', ':')).encode()
    request = urllib.request.Request(url, data=raw, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def check_identity(metadata, source_id, git_base, monitor_hash):
    if any(metadata.get(field) != expected for field, expected in (
            ('source_id', source_id), ('git_base', git_base), ('source_commit', git_base),
            ('monitor_sha256', monitor_hash))):
        raise ValueError('Inference monitor source, Git base or component hash differs from the frozen study')


def check_loaded(state, require_loaded=False):
    models = state.get('models')
    if not isinstance(models, list) or len(models) > 1 or require_loaded and not models:
        raise ValueError('CPU loaded-model state is missing, ambiguous or unexpectedly empty')
    for model in models:
        if (model.get('name') != MODEL or model.get('digest') != DIGEST
                or model.get('context_length') != OPTIONS['num_ctx']
                or type(model.get('size_vram')) is not int or model['size_vram'] != 0):
            raise ValueError('Loaded CPU model digest, context or GPU placement differs from the study')
    return bool(models)


def check_samples(result, prefix, trial_id, clock_domain, source_id, git_base, monitor_hash):
    metadata = result['metadata']
    check_identity(metadata, source_id, git_base, monitor_hash)
    if metadata.get('trial_id') != trial_id or metadata.get('clock_domain') != clock_domain:
        raise ValueError('Readiness observer returned a different session or clock domain')
    bounds = [metadata.get(name) for name in ('started', 'finished')]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds) or bounds[0] > bounds[1]:
        raise ValueError('Readiness observer interval is missing or reversed')
    samples = result['samples']
    if not isinstance(samples, list) or len(samples) < 2:
        raise ValueError('Readiness requires at least two retained native process samples')
    pids = set()
    for sample in samples:
        if 'unavailable' in sample or sample.get('other_server_model_count') != 0:
            raise ValueError('Native inference telemetry or the local other-server observation is unavailable')
        if not sample.get('processes') or not sample.get('linux_cgroups'):
            raise ValueError('Native Ollama process or cgroup evidence is missing')
        pids.update(process['pid'] for process in sample['processes'])
        for group in sample['linux_cgroups'].values():
            verify_cpu_envelope(group, prefix)
    return {'samples': len(samples), 'model_process_pids': sorted(pids),
            'scope': 'Ollama listener11435 and descendants; container aggregate is not model-only RSS.'}


def readiness(prefix, source_id, git_base, *, sample_observer=False, require_loaded=False):
    if not __debug__:
        raise RuntimeError('Readiness verification requires Python assertions')
    campaign = get_cpu_campaign(prefix)
    if campaign['terminal']:
        raise ValueError('A terminal attempt cannot authorize a new campaign')
    snapshot = capture(ROOT)
    verify_worktree(ROOT, snapshot.source_id)
    if snapshot.source_id != source_id or snapshot.git_base != git_base:
        raise ValueError('Local collection source differs from the approved source identity')
    registered = verify_registered(ROOT, source_id)
    monitor_hash = registered['files']['v2/inference_monitor.py']['sha256']
    version = query(campaign['inference_url'] + '/api/version')
    tags = query(campaign['inference_url'] + '/api/tags')
    matching = [model for model in tags['models'] if model.get('name') == MODEL]
    if len(matching) != 1 or matching[0].get('digest') != DIGEST:
        raise ValueError('The pinned model is missing or ambiguous on the private proxy path')
    monitor = query(campaign['monitor_url'] + '/metadata')
    check_identity(monitor, source_id, git_base, monitor_hash)
    if monitor.get('version') != version:
        raise ValueError('Proxy and native monitor server versions differ')
    verify_cpu_metadata({'version': version, 'model': matching[0], 'options': dict(OPTIONS),
                         'client_hardware': hardware(), 'inference_environment': monitor}, prefix)
    proxy_state = query(campaign['inference_url'] + '/api/ps')
    loaded = check_loaded(proxy_state, require_loaded)
    check_loaded(monitor['model_state'], require_loaded)
    result = {'ready': True, 'cohort_prefix': prefix, 'source_id': source_id, 'git_base': git_base,
              'model': MODEL, 'model_digest': DIGEST, 'model_loaded': loaded,
              'generation_requests': 0, 'observer_sampling': None,
              'observed': {'version': version, 'model_tag': matching[0], 'proxy_model_state': proxy_state,
                           'monitor_metadata': monitor},
              'scope': 'GET-only metadata readiness; this is not a transport qualification, pilot or performance result.'}
    if sample_observer:
        trial_id = 'readiness-' + uuid.uuid4().hex
        started = False
        try:
            started = True  # An ambiguous transport failure may follow a successful /start.
            acknowledgement = query(campaign['monitor_url'] + '/start', {'trial_id': trial_id})
            if acknowledgement.get('trial_id') != trial_id or acknowledgement.get('clock_domain') != monitor.get('clock_domain'):
                raise ValueError('Readiness observer session differs from the selected native monitor')
            time.sleep(2.5)
        except HTTPError as error:
            if error.code == 409:
                started = False  # Somebody else's active session must remain untouched.
            raise
        finally:
            # /stop is identity-checked by the monitor; only our UUID can stop our session.
            if started:
                observation = query(campaign['monitor_url'] + '/stop', {'trial_id': trial_id})
        result['observer_sampling'] = check_samples(observation, prefix, trial_id, monitor['clock_domain'],
                                                    source_id, git_base, monitor_hash)
        result['observed']['native_observer'] = observation
        result['scope'] = 'Non-generative metadata and native process readiness; observer session was started and stopped explicitly.'
    result['checked_utc'] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort-prefix', required=True, choices=tuple(CPU_CAMPAIGNS))
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--git-base', required=True)
    parser.add_argument('--sample-observer', action='store_true', help='Explicitly start/stop a2.5s native process observation, without inference')
    parser.add_argument('--require-loaded', action='store_true', help='Require a model already loaded by a separate operator action')
    args = parser.parse_args()
    try:
        result = readiness(args.cohort_prefix, args.source_id, args.git_base,
                           sample_observer=args.sample_observer, require_loaded=args.require_loaded)
    except Exception as error:
        print(json.dumps({'ready': False, 'generation_requests': 0, 'error': type(error).__name__, 'detail': str(error)}), flush=True)
        raise SystemExit(1)
    result['readiness_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
