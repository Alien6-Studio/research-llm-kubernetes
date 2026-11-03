"""Verify lossless archives, frozen workloads and individual measured records offline."""
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from v2.plan import ROOT, canonical, load_trace
from v2.analyze import summarize, analyze
from v2.identity import PREFIX, verify_registered
from v2.cpu_campaigns import CPU_CAMPAIGNS, cpu_campaign_for_plan, get_cpu_campaign


def same_numbers(expected, actual):
    """Allow only rounding from clock-span subtraction on different Python builds."""
    if isinstance(expected, dict):
        assert expected.keys() == actual.keys()
        for key in expected: same_numbers(expected[key], actual[key])
    elif isinstance(expected, list):
        assert len(expected) == len(actual)
        for left, right in zip(expected, actual): same_numbers(left, right)
    elif isinstance(expected, float):
        assert abs(expected - actual) <= 1e-12 * max(1, abs(expected)), (expected, actual)
    else: assert expected == actual, (expected, actual)
from v2.workload import correct, model_request, MODEL, DIGEST, OPTIONS
from v2.run import percentile, validate


def digest(raw): return hashlib.sha256(raw).hexdigest()


def verify_source_metadata(metadata, instruments):
    """Prefer explicit content identity while retaining historical Git-keyed data."""
    content_addressed = 'source_id' in metadata
    if content_addressed:
        source_id = metadata['source_id']
        if not isinstance(source_id, str) or not source_id.startswith(PREFIX):
            raise ValueError('Invalid content source identity')
        base = metadata.get('git_base')
        if not isinstance(base, str) or len(base) not in (40, 64) or any(
            char not in '0123456789abcdef' for char in base
        ) or ('source_commit' in metadata and metadata['source_commit'] != base) or (
            'code_sha256' in metadata and metadata.get('source_commit') != base
        ):
            raise ValueError('Content identity must retain its actual Git base separately')
        entry = verify_registered(ROOT, source_id)
        assert entry == instruments[source_id], 'Content registry lookup mismatch'
    else:
        entry = instruments[metadata['source_commit']]
    captured = entry['files']
    if 'code_sha256' in metadata:
        source_hash = hashlib.sha256()
        names = ['service.py', 'transport.py', 'quota.py', 'workload.py']
        if content_addressed:
            names.append('network_policy.py')
        for name in names:
            source_hash.update(name.encode() + b'\0' + (ROOT / captured['v2/' + name]['path']).read_bytes())
        assert source_hash.hexdigest() == metadata['code_sha256'], 'Executed source identity not preserved'
    if content_addressed:
        for field, name in (('driver_sha256', 'v2/model.py'),
                            ('model_controller_sha256', 'v2/model_service.py'),
                            ('monitor_sha256', 'v2/inference_monitor.py')):
            if field in metadata and (name not in captured or metadata[field] != captured[name]['sha256']):
                raise ValueError('Recorded measured component hash differs from its content snapshot: ' + field)
    return captured


def verify_role_identity(role, metadata):
    if role.get('code_sha256') is not None:
        assert role['code_sha256'] == metadata['code_sha256']
    if role.get('source_commit'):
        assert role['source_commit'] == metadata['source_commit']
    if 'source_id' in metadata:
        if 'source_id' in role or 'unavailable' not in role:
            assert role.get('source_id') == metadata['source_id'], 'Observed pod source identity differs'
        if 'unavailable' not in role:
            assert role.get('source_commit') == metadata['source_commit'], 'Observed pod Git base differs'
        if 'git_base' in role:
            assert role['git_base'] == metadata['git_base'], 'Observed pod Git base differs'


PHASE_ORDER = (
    'request_task_started', 'connection_started', 'connection_established',
    'serialization_started', 'serialization_finished', 'write_started',
    'write_queued', 'emitted', 'status_received', 'headers_received',
    'body_received', 'body_decoded', 'close_started', 'close_complete',
)
CORE_PHASES = tuple(name for name in PHASE_ORDER[:-2]
                    if name not in ('serialization_started', 'serialization_finished', 'write_queued'))


def verify_transport_phases(row):
    """Compare only timestamps explicitly belonging to one requesting process.

    Serialization and write-queue markers are optional: the independent network
    probe records a smaller set than the shared transport. Failure records retain
    only reached stages. Direct Ollama work may drain after the client finishes.
    """
    if 'transport_phases' not in row:
        return  # Historical observations did not capture these optional stages.
    phases = row['transport_phases']
    if not isinstance(phases, dict) or phases.get('clock') != 'time.monotonic':
        raise ValueError('Transport phases must declare their monotonic clock')
    domain = row.get('clock_domain') or phases.get('clock_domain')
    if not isinstance(domain, str) or not domain:
        raise ValueError('Transport phases need an explicit requesting clock domain')
    if phases.get('clock_domain', domain) != domain:
        raise ValueError('Transport phases belong to a different clock domain')
    known = set(PHASE_ORDER) | {'clock', 'clock_scope', 'clock_domain'}
    if set(phases) - known:
        raise ValueError('Unknown transport phase requires an explicit verification rule')
    observed = [name for name in PHASE_ORDER if name in phases]
    if observed and phases.get('clock_scope') != 'requesting_process':
        raise ValueError('Transport phase timestamps must use the requesting process clock')
    for name in observed:
        value = phases[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Transport phase timestamp is not finite')
    times = [phases[name] for name in observed]
    if times != sorted(times):
        raise ValueError('Transport phases are out of order')
    core = [name for name in CORE_PHASES if name in phases]
    if core != list(CORE_PHASES[:len(core)]):
        raise ValueError('Transport phases skip a required preceding stage')
    for name, prerequisite in (
        ('serialization_started', 'connection_established'),
        ('serialization_finished', 'serialization_started'),
        ('write_queued', 'write_started'),
        ('close_started', 'connection_established'),
        ('close_complete', 'close_started'),
    ):
        if name in phases and prerequisite not in phases:
            raise ValueError('Transport phases skip a required preceding stage')
    if 'serialization_started' in phases and 'write_started' in phases and 'serialization_finished' not in phases:
        raise ValueError('Transport write precedes completion of serialization')
    completed = isinstance(row.get('status'), int) and row['status'] >= 100 and not row.get('error')
    if completed or 'late_response' in row:
        if core != list(CORE_PHASES) or 'close_complete' not in phases:
            raise ValueError('Successful transport lacks complete response phases')
    emitted = row.get('emitted')
    if ('emitted' in phases) != (emitted is not None):
        raise ValueError('Recorded emission differs from the transport phases')
    for name in ('launched', 'emitted', 'finished', 'model_call_drained_at'):
        value = row.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError('Request timestamp is not finite')
    # The row's clock_domain explicitly attaches its own times to this process.
    # With a phase-only domain, validate internal order without inferring clocks.
    if row.get('clock_domain'):
        if emitted is not None:
            if emitted < phases['emitted'] or ('status_received' in phases and emitted > phases['status_received']):
                raise ValueError('Emission callback is outside its transport stage')
        if observed and 'launched' in row and phases[observed[0]] < row['launched']:
            raise ValueError('Transport starts before its request task launches')
        finished = row.get('finished')
        drained = row.get('model_call_drained_at')
        if finished is not None and 'launched' in row and finished < row['launched']:
            raise ValueError('Client outcome precedes the request launch')
        if finished is not None and drained is not None and drained < finished:
            raise ValueError('Model drain precedes the client outcome')
        if observed and finished is not None and phases[observed[-1]] > finished:
            if drained is None or row.get('reason') != 'client_deadline':
                raise ValueError('Transport extends after a client outcome without a recorded late drain')
        upper = drained if drained is not None else finished
        if observed and upper is not None and phases[observed[-1]] > upper:
            raise ValueError('Transport extends beyond its recorded completion or drain')
        if emitted is not None and upper is not None and emitted > upper:
            raise ValueError('Emission extends beyond its recorded completion or drain')


def verify_standalone_warmup(data):
    """Replay retained loading attempts without inferring missing generations.

    A failed or interrupted prefix remains invalid and is counted only through
    its recorded observations. A complete successful warmup must also preserve
    the pinned server/model identity and its actually loaded context length.
    """
    from v2.model import pilot_item

    metadata = data['metadata']
    if metadata['version']['version'] != '0.40.16':
        raise ValueError('Warmup server version differs from the pinned version')
    if metadata['model']['name'] != MODEL or metadata['model']['digest'] != DIGEST:
        raise ValueError('Warmup model identity differs from the pinned model')
    if metadata['options'] != OPTIONS:
        raise ValueError('Warmup generation options differ from the pinned options')
    observations = data['observations']
    if not isinstance(observations, list) or len(observations) > 4:
        raise ValueError('Warmup must retain at most its four attempted generations')
    correctness = []
    for index, row in enumerate(observations):
        if row['item'] != pilot_item(index):
            raise ValueError('Warmup item differs from its frozen task')
        status = row['status']
        if isinstance(status, bool) or not isinstance(status, int) or not (status == 0 or 100 <= status <= 599):
            raise ValueError('Warmup HTTP status is invalid')
        elapsed = row['elapsed']
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('Warmup elapsed time must be finite and nonnegative')
        verify_transport_phases(row)
        raw = row['raw']  # Failed attempts must retain their actual response/error too.
        expected = status == 200 and correct(raw, row['item'])
        if not isinstance(row['correct'], bool) or row['correct'] != expected:
            raise ValueError('Warmup correctness differs from its archived answer')
        correctness.append(expected)
    if any(not value for value in correctness[:-1]):
        raise ValueError('Warmup continued after its first failed generation')
    expected_valid = len(observations) == 4 and all(correctness)
    if not isinstance(data['valid'], bool) or data['valid'] != expected_valid:
        raise ValueError('Warmup validity must reflect exactly four correct generations')
    if expected_valid or 'loaded_model' in data:
        loaded = data['loaded_model']['models']
        if not isinstance(loaded, list) or len(loaded) != 1:
            raise ValueError('Warmup must identify its single loaded model')
        model = loaded[0]
        if model.get('name') != MODEL or model.get('digest') != DIGEST:
            raise ValueError('Warmup loaded model identity differs from the pinned model')
        context = model.get('context_length')
        if isinstance(context, bool) or not isinstance(context, int) or context != OPTIONS['num_ctx']:
            raise ValueError('Warmup loaded context differs from the pinned context')
    return len(observations)


def verify_direct_model_answers(row, item):
    """Replay each retained direct-model response using its own HTTP outcome."""
    count = 0
    for name in ('response', 'late_response'):
        if name not in row:
            continue
        body = row[name]
        if not isinstance(body, dict):
            raise ValueError('Direct model response wrapper is invalid')
        if 'inference' not in body:
            continue
        status = row['status'] if name == 'response' else body['status']
        expected = status == 200 and correct(body['inference'], item)
        if not isinstance(body['correct'], bool) or body['correct'] != expected:
            raise ValueError('Direct model correctness differs from its HTTP status and archived answer')
        if name == 'response' and (not isinstance(row['correct'], bool) or row['correct'] != expected):
            raise ValueError('Direct model client correctness differs from its archived answer')
        count += 1
    if count > 1:
        raise ValueError('Direct generation is recorded as both timely and late')
    return count


PLAIN_COHORT = 'v2-ollama-isolated-plain'
PLAIN_PLAN = 'protocol/ollama-isolated-plain-plan.json'
STDIO_COHORT = 'v2-ollama-isolated-stdio'
STDIO_PLAN = 'protocol/ollama-isolated-stdio-plan.json'
CPU_COHORT = 'v2-ollama-dell-cpu'
CPU_PLAN = 'protocol/ollama-dell-cpu-plan.json'
CPU_TRACE_DIRECTORY = 'protocol/ollama-dell-cpu'
CPU_INFERENCE_URL = 'http://172.16.0.244:11437'
CPU_MAX_MEASUREMENT_SECONDS = 120
CPU_BINARY_ARTIFACT = {
    'version': '0.40.16',
    'url': 'https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-linux-amd64.tar.zst',
    'sha256': '87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d',
    'bytes': 1545767640,
}
MODEL_ARTIFACT_PATH = 'protocol/model-artifacts/qwen3.6-07d35212591f.manifest.json'
PLAIN_ATTEMPTS = {
    PLAIN_COHORT: {'plan': PLAIN_PLAN, 'cohort': 'ollama-isolated-plain', 'interrupted': True},
    PLAIN_COHORT + '-restart': {'plan': PLAIN_PLAN, 'cohort': 'ollama-isolated-plain', 'interrupted': False},
    STDIO_COHORT: {'plan': STDIO_PLAN, 'cohort': 'ollama-isolated-stdio', 'interrupted': False},
    **{prefix: {'plan': data['plan_path'], 'cohort': data['cohort'], 'interrupted': data['terminal'], 'fresh_cpu_pilot': True}
       for prefix, data in CPU_CAMPAIGNS.items()},
}


def load_cpu_pilot(prefix=CPU_COHORT):
    """The fresh pilot is bound through its lossless, retained archive bytes."""
    campaign = get_cpu_campaign(prefix)
    relative = campaign.get('retained_calibration_archive', 'archives/' + prefix + '-pilot/pilot.json.gz')
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or str(path.relative_to(ROOT)) != relative:
        raise ValueError('CPU pilot archive path is unsafe')
    manifest_bytes = (path.parent / 'manifest.json').read_bytes()
    if campaign.get('calibration_manifest_sha256') is not None and digest(manifest_bytes) != campaign['calibration_manifest_sha256']:
        raise ValueError('Retained calibration original manifest bytes changed')
    manifest = json.loads(manifest_bytes)
    entries = [entry for entry in manifest['files'] if entry['path'] == relative]
    if len(entries) != 1:
        raise ValueError('CPU pilot archive must identify exactly one pilot document')
    entry = entries[0]
    encoded = path.read_bytes()
    if campaign.get('calibration_archive_sha256') is not None and digest(encoded) != campaign['calibration_archive_sha256']:
        raise ValueError('Retained calibration original archive bytes changed')
    raw = gzip.decompress(encoded)
    if digest(encoded) != entry['sha256'] or digest(raw) != entry['decoded_sha256'] or len(raw) != entry['decoded_bytes']:
        raise ValueError('CPU pilot archive identity mismatch')
    if campaign.get('calibration_pilot_sha256') is not None and digest(raw) != campaign['calibration_pilot_sha256']:
        raise ValueError('Retained calibration differs from the explicitly registered pilot bytes')
    return json.loads(raw), digest(raw)


def verify_retained_cpu_calibration(prefix, collection_source_id, git_base, *, pilot_path=None):
    """Adopt a complete calibration without rewriting its original source identity."""
    from v2.calibration import verify_calibration_compatibility
    from v2.identity import verify_worktree
    from v2.model import cpu_measurement_seconds, validate_pilot
    campaign = get_cpu_campaign(prefix)
    if not campaign.get('retained_calibration_archive'):
        raise ValueError('This campaign does not explicitly adopt retained calibration')
    if collection_source_id != campaign['collection_source_id']:
        raise ValueError('Retained calibration requires the registered extended collection source')
    verify_worktree(ROOT, collection_source_id)
    compatibility = verify_calibration_compatibility(ROOT, campaign['calibration_source_id'], collection_source_id)
    pilot, pilot_hash = load_cpu_pilot(prefix)
    if pilot_path is not None:
        path = (ROOT / pilot_path).resolve()
        if path != (ROOT / campaign['retained_calibration_pilot']).resolve() or digest(path.read_bytes()) != pilot_hash:
            raise ValueError('Retained raw pilot differs from its registered archive')
    if (pilot.get('source_id') != campaign['calibration_source_id']
            or any(pilot.get(field) != git_base for field in ('git_base', 'source_commit'))):
        raise ValueError('Retained pilot must preserve its original calibration source and Git base')
    validate_pilot(pilot)
    calibration_rows = [*pilot['warmup'], *[row for setting in pilot['results'] for row in setting['observations']]]
    if any(not isinstance(row['raw'], dict) or row['raw'].get('done') is not True for row in calibration_rows):
        raise ValueError('Retained calibration must contain all 52 successful terminal native responses')
    verify_cpu_metadata(pilot['metadata'], prefix)
    inference = pilot['metadata'].get('inference_environment', {})
    if (inference.get('source_id') != campaign['calibration_source_id']
            or any(inference.get(field) != git_base for field in ('git_base', 'source_commit'))):
        raise ValueError('Retained calibration monitor identity differs from the original pilot')
    size_vram = pilot['loaded_model']['models'][0].get('size_vram')
    if isinstance(size_vram, bool) or size_vram != 0:
        raise ValueError('Retained calibration does not confirm CPU-only model placement')
    window = validate_cpu_window(cpu_measurement_seconds(pilot), prefix)
    return {'valid': True, 'measurement_seconds': window, 'calibration_source_id': campaign['calibration_source_id'],
            'collection_source_id': collection_source_id, 'calibration_pilot_sha256': pilot_hash,
            'calibration_archive_sha256': campaign['calibration_archive_sha256'],
            'calibration_manifest_sha256': campaign['calibration_manifest_sha256'],
            'calibration_pilot_archive': campaign['retained_calibration_archive'], 'source_compatibility': compatibility}


def verify_cpu_metadata(metadata, prefix=CPU_COHORT):
    """Bind Linux CPU placement to the retained official binary/model records."""
    if metadata['version']['version'] != '0.40.16' or metadata['model']['name'] != MODEL or metadata['model']['digest'] != DIGEST or metadata['options'] != OPTIONS:
        raise ValueError('CPU metadata model, server or options differ from the pinned study')
    inference = metadata.get('inference_environment', {})
    hardware = inference.get('hardware', {})
    if metadata.get('client_hardware', {}).get('system') != 'Linux' or hardware.get('system') != 'Linux':
        raise ValueError('CPU study must retain its Linux client and inference placement')
    verify_cpu_envelope(hardware.get('observer_cgroup', {}), prefix)
    artifacts = hardware.get('deployment_artifacts', {})
    if artifacts.get('ollama_binary') != CPU_BINARY_ARTIFACT:
        raise ValueError('CPU binary artifact differs from the pinned official release')
    raw = (ROOT / MODEL_ARTIFACT_PATH).read_bytes()
    if digest(raw) != DIGEST:
        raise ValueError('Retained CPU model manifest differs from its pinned digest')
    manifest = json.loads(raw)
    expected = {blob['digest']: blob['size'] for blob in [manifest['config'], *manifest['layers']]}
    staged = artifacts.get('model_staging', {})
    blobs = staged.get('blobs', [])
    if (staged.get('model') != MODEL or staged.get('manifest_sha256') != DIGEST or staged.get('manifest_bytes') != len(raw)
            or staged.get('registry_blobs') != 'https://registry.ollama.ai/v2/library/qwen3.6/blobs/'
            or len(blobs) != len(expected) or {blob.get('digest') for blob in blobs} != set(expected)
            or any(blob.get('bytes') != expected.get(blob.get('digest')) or blob.get('verified') is not True for blob in blobs)):
        raise ValueError('CPU staged model blobs differ from the pinned manifest')


def verify_cpu_envelope(group, prefix=CPU_COHORT):
    """Check visible quota/caps, never infer available physical capacity."""
    limits = group.get('visible_hierarchy_limits', {})
    campaign = get_cpu_campaign(prefix)
    if (limits.get('complete') is not True or limits.get('cpu_quota_cores_upper_bound') != campaign['cpu_quota_cores']
            or limits.get('memory_max_bytes_upper_bound') != campaign['inference_memory_bytes']
            or limits.get('memory_swap_max_bytes_upper_bound') != campaign['swap_max_bytes']):
        raise ValueError('CPU visible cgroup envelope is missing or differs from the registered limits')


def replay_cpu_plan(plan):
    """Regenerate the fresh CPU plan without reusing Mac capacity or workloads."""
    if not __debug__:
        raise RuntimeError('CPU plan verification requires Python assertions')
    from v2.model import cpu_measurement_seconds, derive_model_plan
    campaign = cpu_campaign_for_plan(plan)
    prefix = campaign['prefix']
    pilot, pilot_hash = load_cpu_pilot(prefix)
    registration = plan['continuation']
    if prefix != CPU_COHORT and registration.get('campaign_descriptor') != campaign:
        raise ValueError('CPU plan differs from its registered campaign descriptor')
    if prefix != CPU_COHORT and not any(entry.get('path') == 'v2/cpu_campaigns.py' for entry in registration.get('method_sources', [])):
        raise ValueError('CPU plan does not retain its shared descriptor method identity')
    seen_methods = set()
    for entry in registration.get('method_sources', []):
        path = (ROOT / entry['path']).resolve()
        if entry['path'] in seen_methods or not path.is_relative_to(ROOT):
            raise ValueError('CPU campaign method source is duplicate or unsafe')
        seen_methods.add(entry['path'])
        raw = path.read_bytes()
        if digest(raw) != entry['sha256'] or len(raw) != entry['bytes']:
            raise ValueError('CPU campaign method source differs from its frozen identity')
    if plan['pilot_sha256'] != pilot_hash:
        raise ValueError('CPU plan does not bind its own archived pilot bytes')
    calibration_source = campaign.get('calibration_source_id', registration['source_id'])
    if campaign.get('retained_calibration_archive'):
        if not {'v2/calibration.py', 'v2/verify.py'} <= seen_methods:
            raise ValueError('Retained calibration does not retain its complete verification method identities')
        adopted = verify_retained_cpu_calibration(prefix, registration['source_id'], registration['git_base'])
        for key in ('calibration_source_id', 'collection_source_id', 'calibration_pilot_sha256',
                    'calibration_archive_sha256', 'calibration_manifest_sha256', 'source_compatibility'):
            if registration.get(key) != adopted[key]:
                raise ValueError('Retained calibration declaration differs from its verified source or pilot: ' + key)
        if registration.get('pilot_archive') != adopted['calibration_pilot_archive']:
            raise ValueError('Retained calibration must reference its original archive without a duplicate pilot')
        if registration.get('maximum_measurement_seconds') != campaign['maximum_measurement_seconds']:
            raise ValueError('Extended collection changed its exact registered measurement window')
    if pilot.get('source_id') != calibration_source or any(
            pilot.get(field) != registration['git_base'] for field in ('git_base', 'source_commit')):
        raise ValueError('CPU pilot source identity or Git base differs from its campaign')
    metadata = pilot['metadata']
    verify_cpu_metadata(metadata, prefix)
    inference = metadata.get('inference_environment', {})
    if inference.get('source_id') != calibration_source:
        raise ValueError('CPU pilot inference monitor source differs from its campaign')
    if any(inference.get(field) != registration['git_base'] for field in ('git_base', 'source_commit')):
        raise ValueError('CPU pilot inference monitor Git base differs from its campaign')
    loaded = pilot.get('loaded_model', {}).get('models', [])
    if len(loaded) != 1 or isinstance(loaded[0].get('size_vram'), bool) or loaded[0].get('size_vram') != 0:
        raise ValueError('CPU pilot does not confirm zero GPU-resident model bytes')
    try:
        began = datetime.fromisoformat(pilot['utc'].replace('Z', '+00:00'))
        frozen = datetime.fromisoformat(registration['frozen_utc'].replace('Z', '+00:00'))
        if began.utcoffset() is None or frozen.utcoffset() is None or began > frozen:
            raise ValueError('Pilot start must precede prospective plan freeze')
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError('CPU pilot/plan UTC ordering is missing or inconsistent') from error

    def retained_trace(relative, expected_items):
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('CPU trace escapes the repository')
        encoded = path.read_bytes()
        actual_items = json.loads(gzip.decompress(encoded))
        same_numbers(expected_items, actual_items)
        return encoded

    window = validate_cpu_window(cpu_measurement_seconds(pilot), prefix)
    expected, _ = derive_model_plan(pilot, pilot_hash, campaign['trace_directory'],
                                    measurement_seconds=window,
                                    provider_url=campaign['inference_url'], encode_trace=retained_trace)
    if any(plan.get(field) != value for field, value in expected.items()):
        raise ValueError('CPU plan differs from its fresh pilot, prospective window or generated workloads')
    if registration.get('transport_control') != campaign['inference_url'] + '/transport-control':
        raise ValueError('CPU transport control differs from the registered private inference path')
    return expected


def validate_cpu_window(seconds, prefix=None):
    campaign = get_cpu_campaign(prefix) if prefix is not None else {}
    maximum = campaign.get('maximum_measurement_seconds', CPU_MAX_MEASUREMENT_SECONDS)
    if type(seconds) is not int or seconds <= 0 or seconds > maximum:
        raise ValueError(f'CPU pilot-derived measurement window exceeds the registered {maximum}-second feasibility cap')
    if campaign.get('measurement_seconds') is not None and seconds != campaign['measurement_seconds']:
        raise ValueError('Retained calibration must reproduce the exact registered 335-second window')
    return seconds


def replay_native_completion_certificate(config, result, certificate, identity):
    """Reconcile archived completion evidence without proxy requests or Ollama."""
    from v2.model import expected_native_calls, validate_proxy_state
    if not isinstance(certificate, dict) or certificate.get('valid') is not True:
        return False
    if certificate.get('schema') != 'alien6-native-completion-v1' or certificate.get('issues') != []:
        raise ValueError('Native completion certificate schema or successful claim is inconsistent')
    before = certificate.get('pre_state')
    validate_proxy_state(before, identity)
    if before['active'] or before['uncertain_total']:
        raise ValueError('Native completion certificate did not start from certified idle state')
    expected = expected_native_calls(config, result)
    if certificate.get('expected') != expected:
        raise ValueError('Native completion certificate expected-call coverage is not reproducible')
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    began, deadline, finished = (certificate.get(name) for name in ('driver_began', 'driver_deadline', 'driver_finished'))
    if (not all(finite(value) for value in (began, deadline, finished))
            or certificate.get('budget_seconds') != 900 or abs(deadline - began - 900) > 1e-6
            or not began <= finished <= deadline):
        raise ValueError('Native completion certificate exceeded or changed its driver budget')
    clock = certificate.get('driver_clock', {})
    if (clock.get('clock') != 'time.monotonic' or clock.get('scope') != 'requesting_VM_process'
            or type(clock.get('pid')) is not int or clock['pid'] <= 0
            or not isinstance(clock.get('clock_domain'), str) or not clock['clock_domain']):
        raise ValueError('Native completion certificate driver clock is missing')
    baseline = before['started_total']
    previous, previous_at = before, began
    polls = certificate.get('summary_polls')
    if not isinstance(polls, list) or not polls:
        raise ValueError('Native completion certificate summary observations are missing')
    for poll in polls:
        at, state = poll.get('driver_at'), poll.get('state')
        if not finite(at) or not previous_at <= at <= finished:
            raise ValueError('Native completion certificate driver observations are not ordered')
        validate_proxy_state(state, identity, previous)
        if (state.get('clock') != 'time.monotonic' or state['uncertain_total'] != 0
                or state['started_total'] - baseline > expected['count']
                or state['finished_total'] - before['finished_total'] > expected['count']
                or any(row['sequence'] <= baseline for row in state['active'])):
            raise ValueError('Native completion certificate contains unaccounted or uncertain calls')
        previous, previous_at = state, at
    terminal = certificate.get('terminal_state')
    validate_proxy_state(terminal, identity, previous)
    if (terminal.get('clock') != 'time.monotonic' or terminal['active'] or terminal['uncertain_total'] != 0
            or terminal['started_total'] - baseline != expected['count']
            or terminal['finished_total'] - before['finished_total'] != expected['count']):
        raise ValueError('Native completion certificate terminal counters are incomplete')
    records = terminal.get('records')
    if not isinstance(records, list) or len(records) != expected['count']:
        raise ValueError('Native completion certificate terminal responses are missing')
    if Counter(row.get('sequence') for row in records) != Counter(range(baseline + 1, terminal['started_total'] + 1)):
        raise ValueError('Native completion certificate terminal sequences are incomplete or duplicated')
    if Counter(row.get('request_sha256') for row in records) != Counter(expected['request_sha256_counts']):
        raise ValueError('Native completion certificate terminal payload multiset differs')
    for row in records:
        if (row.get('status') != 200 or row.get('done') is not True or row.get('error') is not None
                or row.get('uncertain') is not False or not isinstance(row.get('response'), dict)
                or row['response'].get('done') is not True or not finite(row.get('started'))
                or not finite(row.get('finished')) or row['finished'] < row['started']):
            raise ValueError('Native completion certificate has no complete successful native terminal')
    return True


def replay_network_control(data):
    """Recalculate the retained request identities, percentiles and gate flags."""
    fixture = data['endpoint'].endswith('/transport-control')
    references = None
    if 'plan_path' in data:
        from v2.network_probe import headroom_references
        plan_path = (ROOT / data['plan_path']).resolve()
        references, identity = headroom_references(plan_path)
        assert identity['plan_path'] == data['plan_path'] and identity['plan_sha256'] == data['plan_sha256']
    passes = []
    for trial in data['trials']:
        if references is None:
            trace_path = ROOT / f"protocol/v2-model/headroom-{trial['repetition']}.json.gz"
        else:
            reference = references[trial['repetition']]
            assert trial['trace_reference'] == reference
            trace_path = ROOT / reference['path']
        assert digest(trace_path.read_bytes()) == trial['trace_sha256']
        trace = {row['id']: row for row in json.loads(gzip.decompress(trace_path.read_bytes()))}
        assert len(trial['requests']) == len(trace) and {row['id'] for row in trial['requests']} == set(trace)
        for row in trial['requests']:
            verify_transport_phases(row)
            payload = model_request(trace[row['id']]) if fixture else {'model': MODEL, 'benchmark_padding': trace[row['id']]['prompt_text']}
            body = json.dumps(payload, separators=(',', ':')).encode()
            if row['emitted'] is not None:
                assert digest(body) == row['request_body_sha256']
                assert len(body) == row['request_body_bytes']
            same_numbers(row['finished'] - row['scheduled'], row['latency_seconds'])
        lag = percentile([row['emitted'] - row['scheduled'] for row in trial['requests'] if row['emitted'] is not None], .95)
        latency = percentile([row['latency_seconds'] for row in trial['requests']], .95)
        same_numbers(lag, trial['emission_lag_p95_seconds'])
        same_numbers(latency, trial['latency_p95_seconds'])
        passed = (bool(trial['requests']) and all(row['status'] == 200 and row.get('transport_fixture_returned' if fixture else 'model_metadata_returned')
                  and row['emitted'] is not None and 'error' not in row for row in trial['requests'])
                  and lag is not None and lag <= .05 and latency is not None and latency <= .1)
        assert passed == trial['passed']
        passes.append(passed)
    assert data['qualified'] == all(passes)
    return all(passes)


def replay_continuation_trial_gate(document, source_id, controller_hash, git_base=None):
    """Replay the frozen collection gates, including its native-model checks."""
    config, result = document['configuration'], document['result']
    issues = validate(config, result, document['routing'], document['observations'])
    expected = {pod['uid'] for pod in document['routing']['pods']}
    hit = {row['uid'] for key, archive in result['role_records'].items() if key != 'provider'
           for row in archive['records']}
    if config['block'] == 'real-model' and hit <= expected:
        issues = [issue for issue in issues if issue != 'observed_worker_uid_set_differs']
    roles = [role for sample in result['resource_samples']
             for role in [sample['controller'], sample['provider'], *sample['workers']]]
    if not roles or any(role.get('source_id') != source_id for role in roles):
        issues.append('collection_source_identity_mismatch')
    if any(sample['controller'].get('model_controller_sha256') != controller_hash
           for sample in result['resource_samples']):
        issues.append('model_controller_hash_mismatch')
    native = document.get('native_observations', [])
    monitor = document.get('inference_observer_metadata', {})
    if not native or any('unavailable' in sample for sample in native):
        issues.append('inference_process_observations_missing')
    if any(sample.get('other_server_model_count') != 0 for sample in native):
        issues.append('other_ollama_server_not_observed_idle')
    if monitor.get('source_id') != source_id:
        issues.append('native_monitor_source_identity_mismatch')
    if git_base is not None and (monitor.get('git_base') != git_base or monitor.get('source_commit') != git_base):
        issues.append('native_monitor_git_base_mismatch')
    if monitor.get('stop_error'):
        issues.append('native_monitor_stop_failed')
    if config['block'] == 'real-model':
        loaded = [model for model in document.get('model_after_trial', {}).get('models', [])
                  if model.get('name') == MODEL]
        if len(loaded) != 1 or loaded[0].get('digest') != DIGEST or loaded[0].get('context_length') != OPTIONS['num_ctx']:
            issues.append('loaded_model_identity_or_context_changed')
    return sorted(set(issues))


def load_plain_continuation_documents():
    """Read explicitly mapped attempts, checking each retained byte identity."""
    archives = {}
    for prefix in PLAIN_ATTEMPTS:
        for suffix in ('pilot', 'network', 'qualification', 'warmup', 'real-model'):
            name = prefix + '-' + suffix
            manifest = ROOT / 'archives' / name / 'manifest.json'
            if not manifest.exists():
                continue
            documents = []
            for entry in json.loads(manifest.read_text())['files']:
                path = (ROOT / entry['path']).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():
                    raise ValueError('Missing or unsafe continuation archive file')
                encoded = path.read_bytes()
                raw = gzip.decompress(encoded)
                if digest(encoded) != entry['sha256'] or digest(raw) != entry['decoded_sha256'] or len(raw) != entry['decoded_bytes']:
                    raise ValueError('Continuation archive identity differs from its retained manifest')
                if path.name.endswith('.json.gz'):
                    documents.append(json.loads(raw))
            archives[name] = documents
    if archives:
        instruments = json.loads((ROOT / 'instruments/manifest.json').read_text())['sources']
        for documents in archives.values():
            for document in documents:
                if 'source_id' in document:
                    verify_source_metadata(document, instruments)
                monitor = document.get('inference_observer_metadata', {})
                if 'source_id' in monitor:
                    verify_source_metadata(monitor, instruments)
    return archives


def audit_plain_continuation(archives=None, *, prefix=PLAIN_COHORT):
    """Require the same frozen study and prerequisite gates for qualified claims.

    Failed or partial cohorts remain inspectable and excluded. Only a false
    qualified claim raises. UTC checks are ordering/consistency checks; unavailable
    finish anchors are reported rather than reconstructed from another host's
    monotonic clock. Explicitly matching monotonic domains may be compared.
    """
    if not __debug__:
        raise RuntimeError('Run scientific continuation verification without Python optimization')
    if prefix not in PLAIN_ATTEMPTS:
        raise ValueError('Continuation attempt is not explicitly registered for audit')
    if archives is None:
        archives = load_plain_continuation_documents()
    if not any(prefix + '-' + stage in archives for stage in ('pilot', 'network', 'qualification', 'warmup', 'real-model')):
        return None
    attempt = PLAIN_ATTEMPTS[prefix]
    if attempt['interrupted']:
        documents = archives.get(prefix + '-real-model', [])
        blocks = [data for data in documents if 'complete' in data and 'qualified' in data]
        if any(block.get('qualified') is True for block in blocks):
            raise ValueError('Interrupted continuation attempt cannot claim qualified results')
        counts = {}
        for stage in ('qualification', 'real-model'):
            counts[stage] = sum('configuration' in data and 'result' in data
                                for data in archives.get(prefix + '-' + stage, []))
        network = [data for data in archives.get(prefix + '-network', []) if 'endpoint' in data and 'trials' in data]
        warmups = [data for data in archives.get(prefix + '-warmup', []) if 'observations' in data and 'metadata' in data]
        return {'cohort': prefix, 'eligible': False, 'claimed_qualified': False,
                'issues': ['attempt_interrupted_before_completion'],
                'network_repetitions': sum(len(data['trials']) for data in network),
                'qualification_trials': counts['qualification'],
                'warmup_observations': sum(len(data['observations']) for data in warmups),
                'real_model_trials': counts['real-model'],
                'chronology': {'checked': [], 'unavailable': [
                    'Interrupted attempt is excluded in full; successful prerequisite gates are not required for retention.']}}
    issues, chronology, unavailable = [], [], []
    claims = {}

    def issue(code):
        if code not in issues:
            issues.append(code)

    def single(stage, predicate, label):
        matches = [data for data in archives.get(prefix + '-' + stage, []) if predicate(data)]
        if len(matches) != 1:
            issue(stage + '_' + label + '_missing_or_duplicate')
            return {}
        return matches[0]

    network = single('network', lambda d: 'endpoint' in d and 'trials' in d, 'control')
    warmup = single('warmup', lambda d: 'observations' in d and 'metadata' in d, 'warmup')
    manifests, blocks, trials = {}, {}, {}
    for stage in ('qualification', 'real-model'):
        manifests[stage] = single(stage, lambda d: 'plan_sha256' in d and 'source_commit' in d, 'manifest')
        blocks[stage] = single(stage, lambda d: 'complete' in d and 'qualified' in d, 'block')
        claims[stage] = any(data.get('qualified') is True for data in archives.get(prefix + '-' + stage, [])
                            if 'complete' in data)
        trials[stage] = [data for data in archives.get(prefix + '-' + stage, [])
                         if 'configuration' in data and 'result' in data]
    try:
        raw_plan = (ROOT / attempt['plan']).read_bytes()
        plan = json.loads(raw_plan)
        registration = plan['continuation']
        source_id, git_base = registration['source_id'], registration['git_base']
        plan_hash = digest(raw_plan)
        if attempt.get('fresh_cpu_pilot'):
            expected = replay_cpu_plan(plan)
        else:
            from v2.remote_plan import derive
            expected = derive('172.18.0.1')
        if any(plan.get(field) != value for field, value in expected.items()):
            issue('frozen_plan_differs_from_registered_study')
        if registration.get('cohort') != attempt['cohort'] or (not attempt.get('fresh_cpu_pilot') and registration.get('ssh_compression') is not False):
            issue('frozen_transport_identity_mismatch')
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, AssertionError):
        plan, registration, source_id, git_base, plan_hash = {}, {}, None, None, None
        issue('frozen_plan_missing_or_invalid')

    for label, document in [('network', network), ('warmup', warmup), *manifests.items()]:
        if not document:
            continue
        if not source_id or document.get('source_id') != source_id:
            issue(label + '_source_identity_mismatch')
        if not git_base or document.get('git_base') != git_base or document.get('source_commit') != git_base:
            issue(label + '_git_base_mismatch')
        if label in manifests and (not plan_hash or document.get('plan_sha256') != plan_hash):
            issue(label + '_plan_hash_mismatch')
    if attempt.get('fresh_cpu_pilot') and (network.get('plan_path') != attempt['plan'] or network.get('plan_sha256') != plan_hash):
        issue('network_fresh_cpu_plan_identity_mismatch')
    if attempt.get('fresh_cpu_pilot'):
        for label, document in [('warmup', warmup), *manifests.items()]:
            try:
                verify_cpu_metadata(document['metadata'], prefix)
            except (OSError, KeyError, TypeError, ValueError):
                issue(label + '_cpu_runtime_metadata_mismatch')
        loaded = warmup.get('loaded_model', {}).get('models', [])
        if len(loaded) != 1 or isinstance(loaded[0].get('size_vram'), bool) or loaded[0].get('size_vram') != 0:
            issue('warmup_cpu_execution_not_confirmed')

    net_trials = network.get('trials', [])
    try:
        replayed_network = replay_network_control(network)
    except (OSError, KeyError, TypeError, ValueError, AssertionError):
        replayed_network = False
    network_ok = (len(net_trials) == 3 and {row.get('repetition') for row in net_trials} == {0, 1, 2}
                  and replayed_network and network.get('qualified') is True
                  and network.get('endpoint') == registration.get('transport_control'))
    if not network_ok:
        issue('network_prerequisite_not_qualified')
    try:
        warmup_ok = verify_standalone_warmup(warmup) == 4 and warmup['valid'] is True
    except (KeyError, TypeError, ValueError, AssertionError):
        warmup_ok = False
    if not warmup_ok:
        issue('warmup_prerequisite_not_qualified')

    stage_ok = {}
    for stage, block_name, size in [('qualification', 'model-qualification', 12), ('real-model', 'real-model', 45)]:
        expected_rows = plan.get('blocks', {}).get(block_name, [])
        expected_configs = {row['trial_id']: row for row in expected_rows}
        seen, valid_trials = [], True
        for trial in trials[stage]:
            config = trial['configuration']
            trial_id = config.get('trial_id')
            seen.append(trial_id)
            frozen = {key: value for key, value in config.items()
                      if key not in ('trace', 'trace_decoded_sha256', 'worker_addresses')}
            if frozen != expected_configs.get(trial_id):
                issue(stage + '_trial_configuration_mismatch')
                valid_trials = False
            try:
                replayed = replay_continuation_trial_gate(trial, source_id, manifests[stage].get('model_controller_sha256'), git_base)
            except (KeyError, TypeError, ValueError, AssertionError):
                replayed = ['gate_record_missing_or_invalid']
            if attempt.get('fresh_cpu_pilot'):
                try:
                    native = trial['native_observations']
                    if not native:
                        raise ValueError('Native model observations missing')
                    for snapshot in native:
                        groups = snapshot['linux_cgroups']
                        if not groups:
                            raise ValueError('Native model cgroup hierarchy missing')
                        for group in groups.values():
                            verify_cpu_envelope(group, prefix)
                except (KeyError, TypeError, ValueError):
                    replayed.append('cpu_model_cgroup_envelope_not_confirmed')
            if attempt.get('fresh_cpu_pilot') and stage == 'real-model':
                loaded = trial.get('model_after_trial', {}).get('models', [])
                if len(loaded) != 1 or isinstance(loaded[0].get('size_vram'), bool) or loaded[0].get('size_vram') != 0:
                    replayed.append('cpu_model_execution_not_confirmed')
                if get_cpu_campaign(prefix).get('retained_calibration_archive'):
                    try:
                        proof = registration['source_compatibility']
                        certificate_identity = {'source_id': source_id, 'git_base': git_base,
                                                'proxy_sha256': proof['collection_proxy_sha256']}
                        if not replay_native_completion_certificate(config, trial['result'],
                                trial.get('native_completion_certificate'), certificate_identity):
                            replayed.append('native_completion_not_certified')
                            issue(stage + '_native_completion_not_certified')
                    except (KeyError, TypeError, ValueError):
                        replayed.append('native_completion_certificate_invalid')
                        issue(stage + '_native_completion_certificate_invalid')
            if replayed or trial.get('valid') is not True or trial.get('issues') != []:
                valid_trials = False
            if trial.get('valid') is True and replayed:
                issue(stage + '_trial_validity_claim_not_reproduced')
        complete_grid = (len(expected_rows) == size and len(seen) == size and len(set(seen)) == size
                         and set(seen) == set(expected_configs))
        block = blocks[stage]
        stage_ok[stage] = (complete_grid and valid_trials and block.get('complete') is True
                           and block.get('qualified') is True and block.get('trials') == size
                           and block.get('invalid') == [])
        if not stage_ok[stage]:
            issue(stage + '_block_incomplete_or_unqualified')
        if claims[stage] and not stage_ok[stage]:
            raise ValueError('False qualified continuation ' + stage + ' claim: ' + ', '.join(sorted(issues)))

    def utc(label, value):
        if value is None:
            unavailable.append(label)
            issue(label + '_missing')
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.utcoffset() is None:
                raise ValueError('Naive timestamp')
            return parsed
        except (AttributeError, TypeError, ValueError):
            issue(label + '_invalid')
            return None

    starts = [utc(label, value) for label, value in [
        ('plan_frozen_utc', registration.get('frozen_utc')), ('network_start_utc', network.get('utc')),
        ('qualification_start_utc', manifests['qualification'].get('utc')),
        ('warmup_start_utc', warmup.get('utc')), ('real_model_start_utc', manifests['real-model'].get('utc'))]]
    for index, label in enumerate(('freeze_before_network', 'network_before_qualification',
                                   'qualification_before_warmup', 'warmup_before_real_model')):
        left, right = starts[index:index + 2]
        if left is not None and right is not None:
            chronology.append(label + ': UTC start ordering only')
            if left > right:
                issue(label + '_utc_order_violation')
    if warmup_ok and starts[3] is not None and starts[4] is not None:
        lower_end = starts[3] + timedelta(seconds=sum(row['elapsed'] for row in warmup['observations']))
        chronology.append('warmup minimum finish before real-model start: sequential elapsed lower bound')
        if lower_end > starts[4]:
            issue('warmup_duration_overlaps_real_model_start')

    def monotonic_bounds(rows, lower_key, upper_key):
        result = {}
        for row in rows:
            domain = row.get('clock_domain')
            lower, upper = row.get(lower_key), row.get(upper_key)
            if not domain or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                     and math.isfinite(value) for value in (lower, upper)):
                issue('continuation_monotonic_interval_missing_or_invalid')
                continue
            if lower > upper:
                issue('continuation_monotonic_interval_reversed')
            result.setdefault(domain, []).append((lower, upper))
        return result

    def ordered_domains(label, before, after):
        common = before.keys() & after.keys()
        if not common:
            unavailable.append(label + ': no shared explicit monotonic domain')
        for domain in common:
            chronology.append(label + ': shared monotonic domain')
            if max(upper for _, upper in before[domain]) > min(lower for lower, _ in after[domain]):
                issue(label + '_monotonic_order_violation')

    network_rows = [row for trial in net_trials for row in trial.get('requests', [])]
    qualification_rows = [row for trial in trials['qualification'] for row in trial.get('result', {}).get('requests', [])]
    ordered_domains('network_finished_before_qualification_requests',
                    monotonic_bounds(network_rows, 'launched', 'finished'),
                    monotonic_bounds(qualification_rows, 'launched', 'finished'))
    ordered_domains('qualification_monitor_finished_before_real_model_monitor',
                    monotonic_bounds([trial.get('inference_observer_metadata', {}) for trial in trials['qualification']], 'started', 'finished'),
                    monotonic_bounds([trial.get('inference_observer_metadata', {}) for trial in trials['real-model']], 'started', 'finished'))
    unavailable.extend(['Network has no UTC finish anchor', 'Warmup has no exact UTC finish anchor'])
    result = {'cohort': prefix, 'eligible': not issues, 'claimed_qualified': claims['real-model'],
              'issues': sorted(issues), 'network_repetitions': len(net_trials),
              'qualification_trials': len(trials['qualification']), 'warmup_observations': len(warmup.get('observations', [])),
              'real_model_trials': len(trials['real-model']),
              'chronology': {'checked': chronology, 'unavailable': unavailable}}
    if claims['real-model'] and issues:
        raise ValueError('Qualified real-model continuation lacks its prerequisites: ' + ', '.join(sorted(issues)))
    return result


def audit_plain_continuations(archives=None):
    """Keep interrupted and restarted attempts separate under the same plan."""
    if archives is None:
        archives = load_plain_continuation_documents()
    result = {}
    for prefix in PLAIN_ATTEMPTS:
        audit = audit_plain_continuation(archives, prefix=prefix)
        if audit is not None:
            result[prefix] = audit
    return result


def verify():
    if __debug__ is False:
        raise RuntimeError('Run scientific verification without Python optimization: assertions are required')
    instruments = json.loads((ROOT / 'instruments/manifest.json').read_text())['sources']
    for source_id, source in instruments.items():
        if source_id.startswith(PREFIX):
            verify_registered(ROOT, source_id)
        for entry in source['files'].values():
            path = (ROOT / entry['path']).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError('Missing or unsafe instrument source')
            raw = path.read_bytes()
            if digest(raw) != entry['sha256'] or len(raw) != entry['bytes']:
                raise ValueError('Instrument source identity mismatch')
    files, trials, predictions = 0, 0, 0
    blocks = []
    continuation_archives = {}
    for manifest_path in sorted((ROOT / 'archives').glob('v2-*/manifest.json')):
        entries = json.loads(manifest_path.read_text())['files']
        documents = []
        for entry in entries:
            path = (ROOT / entry['path']).resolve()
            assert path.is_relative_to(ROOT), 'Archive path escapes repository'
            encoded = path.read_bytes()
            assert digest(encoded) == entry['sha256'], path
            raw = gzip.decompress(encoded)
            assert digest(raw) == entry['decoded_sha256'], path
            assert len(raw) == entry['decoded_bytes'], path
            files += 1
            if path.name.endswith('.json.gz'):
                documents.append(json.loads(raw))
        if any(manifest_path.parent.name.startswith(prefix + '-') for prefix in PLAIN_ATTEMPTS):
            continuation_archives[manifest_path.parent.name] = documents
        for data in documents:
            if 'source_id' in data:
                verify_source_metadata(data, instruments)
            if 'observations' in data and 'metadata' in data and 'valid' in data:
                predictions += verify_standalone_warmup(data)
            if 'warmup' in data and 'results' in data and 'metadata' in data:
                observations = data['warmup'] + [row for setting in data['results'] for row in setting['observations']]
                for row in observations:
                    verify_transport_phases(row)
                    assert row['correct'] == (row['status'] == 200 and correct(row['raw'], row['item']))
                    predictions += 1
                for setting in data['results']:
                    assert setting['completion_rate'] == len(setting['observations']) / setting['elapsed_seconds']
            if data.get('endpoint') and 'trials' in data:
                replay_network_control(data)
        block = next((d for d in documents if 'complete' in d and 'qualified' in d), None)
        metadata = next((d for d in documents if 'source_commit' in d and 'code_sha256' in d), None)
        if metadata:
            verify_source_metadata(metadata, instruments)
        count = 0
        for document in documents:
            if 'configuration' not in document or 'result' not in document: continue
            config, result = document['configuration'], document['result']
            reference = config['trace_reference']
            trace = load_trace(reference, config['fastpath'])
            assert trace == config['trace'], 'Recorded workload differs from frozen inputs'
            assert digest(canonical(trace)) == config['trace_decoded_sha256']
            summarize(document)  # Independently rechecks cohorts, latency arithmetic and ledger reconciliation.
            for row in result['requests']:
                verify_transport_phases(row)
            for archive in result['role_records'].values():
                for row in archive['records']:
                    verify_transport_phases(row)
            if metadata:
                monitor = document.get('inference_observer_metadata', {})
                if 'source_id' in monitor:
                    verify_source_metadata(monitor, instruments)
                for sample in result['resource_samples']:
                    for role in [sample['controller'], sample['provider'], *sample['workers']]:
                        verify_role_identity(role, metadata)
                        if role.get('trial_binding'):
                            assert role['trial_id'] == config['trial_id']
                if any(sample['controller'].get('trial_binding') for sample in result['resource_samples']):
                    for archive in result['role_records'].values():
                        assert all(row['trial_id'] == config['trial_id'] for row in archive['records'])
            items = {item['id']: item for item in trace}
            if config['provider']['mode'] == 'ollama':
                for row in result['role_records']['provider']['records']:
                    if 'inference' in row:
                        assert row['correct'] == (row['status'] == 200 and correct(row['inference'], items[row['id']]))
                        predictions += 1
                if config.get('direct_ollama'):
                    for row in result['requests']:
                        predictions += verify_direct_model_answers(row, items[row['id']])
            count += 1
            trials += 1
        if block and block['complete']:
            assert count == block['trials'], (manifest_path, count, block['trials'])
        analysis_path = ROOT / 'analysis' / manifest_path.parent.name / 'results.json'
        if analysis_path.exists(): same_numbers(json.loads(analysis_path.read_text()), analyze(manifest_path.parent))
        blocks.append({'archive': str(manifest_path.relative_to(ROOT)), 'trials': count,
                       'complete': block.get('complete') if block else None,
                       'qualified': block.get('qualified') if block else None})
    continuation_audits = audit_plain_continuations(continuation_archives)
    expected = json.loads((ROOT / 'v2/evidence-counts.json').read_text())
    observed = {'files': files, 'trials': trials, 'model_predictions': predictions,
                'blocks': {b['archive']: b['trials'] for b in blocks}}
    if observed != expected:
        raise ValueError(f'Incomplete published evidence: {observed}')
    report_path = ROOT / 'analysis/v2-study/results.json'
    if not report_path.is_file(): raise ValueError('Missing published study analysis')
    if report_path.exists():
        from v2.report import prepare, illustrate_deadline_loss
        report = prepare()
        report['deadline_illustration'] = illustrate_deadline_loss()
        same_numbers(json.loads(report_path.read_text()), report)
    result = {'verified_files': files, 'verified_trials': trials, 'verified_model_predictions': predictions,
              'blocks': blocks, 'scope': 'Identity and deterministic record replay, not a new measurement or proof of causal validity.'}
    if continuation_audits:
        result['continuation_gate_audits'] = continuation_audits
    return result


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
