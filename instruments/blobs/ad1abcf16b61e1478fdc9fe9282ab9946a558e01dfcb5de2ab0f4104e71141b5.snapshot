"""Freeze the fresh CPU campaign and its lossless pilot before transport trials."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2.identity import capture, verify_worktree
from v2.cpu_campaigns import CPU_CAMPAIGNS, cpu_campaign_for_plan, get_cpu_campaign
from v2.model import cpu_measurement_seconds, derive_model_plan, validate_pilot
from v2.plan import ROOT
from v2.verify import CPU_COHORT, CPU_INFERENCE_URL, CPU_MAX_MEASUREMENT_SECONDS, CPU_PLAN, CPU_TRACE_DIRECTORY, replay_cpu_plan, validate_cpu_window, verify_cpu_metadata, verify_retained_cpu_calibration


class CPUPlanningInfeasible(ValueError):
    def __init__(self, record):
        self.record = record
        super().__init__('Pilot-derived CPU window exceeds 120 seconds; pilot and planning decision retained, no comparison trials launched')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(path):
    path = (ROOT / path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Campaign inputs and outputs must stay inside the repository')
    return path


def exclusive_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)


def archive_pilot(raw_pilot, pilot_path, archive, frozen):
    encoded = gzip.compress(raw_pilot, compresslevel=9, mtime=0)
    pilot_archive = archive / 'pilot.json.gz'
    exclusive_write(pilot_archive, encoded)
    entry = {'path': str(pilot_archive.relative_to(ROOT)), 'source_relative_path': str(pilot_path.relative_to(ROOT)),
             'sha256': digest(encoded), 'decoded_sha256': digest(raw_pilot), 'decoded_bytes': len(raw_pilot), 'bytes': len(encoded)}
    exclusive_write(archive / 'manifest.json', (json.dumps({'exported_at_utc': frozen,
        'format': 'Lossless gzip copy of the complete native CPU pilot; raw observations are unchanged.',
        'files': [entry]}, indent=2) + '\n').encode())


def freeze_campaign(pilot_path, output_path, method_files=(), *, cohort_prefix=CPU_COHORT):
    if not __debug__:
        raise RuntimeError('Scientific plan freezing requires Python assertions')
    campaign = get_cpu_campaign(cohort_prefix)
    if campaign['terminal']:
        raise ValueError('The previous CPU attempt is terminal; use a registered fresh campaign')
    pilot_path, output_path = inside(pilot_path), inside(output_path)
    if output_path != (ROOT / campaign['plan_path']).resolve():
        raise ValueError('Use the registered flat CPU plan path')
    trace_root = ROOT / campaign['trace_directory']
    archive = ROOT / 'archives' / (cohort_prefix + '-pilot')
    if output_path.exists() or trace_root.exists() or archive.exists():
        raise FileExistsError('Refusing to overwrite an existing CPU plan, trace set or pilot archive')
    snapshot = capture(ROOT)
    verify_worktree(ROOT, snapshot.source_id)
    raw_pilot = pilot_path.read_bytes()
    pilot = json.loads(raw_pilot)
    validate_pilot(pilot)
    verify_cpu_metadata(pilot['metadata'], cohort_prefix)
    adopted = None
    if campaign.get('retained_calibration_archive'):
        adopted = verify_retained_cpu_calibration(cohort_prefix, snapshot.source_id, snapshot.git_base, pilot_path=pilot_path)
    elif pilot.get('source_id') != snapshot.source_id or any(pilot.get(field) != snapshot.git_base for field in ('git_base', 'source_commit')):
        raise ValueError('Fresh CPU pilot differs from the registered collection source')
    loaded = pilot['loaded_model']['models'][0]
    if isinstance(loaded.get('size_vram'), bool) or loaded.get('size_vram') != 0:
        raise ValueError('Fresh CPU pilot does not confirm CPU-only model placement')
    methods = []
    method_files = list(method_files)
    automatic_methods = ['v2/cpu_campaigns.py']
    if adopted is not None:
        automatic_methods += ['v2/calibration.py', 'v2/verify.py']
    for name in automatic_methods:
        path = inside(name)
        if path not in [inside(value) for value in method_files]:
            method_files.append(path)
    for value in method_files:
        path = inside(value)
        raw = path.read_bytes()
        relative = str(path.relative_to(ROOT))
        if any(entry['path'] == relative for entry in methods):
            raise ValueError('Duplicate campaign method source')
        methods.append({'path': relative, 'sha256': digest(raw), 'bytes': len(raw)})
    methods.sort(key=lambda entry: entry['path'])
    window = cpu_measurement_seconds(pilot)
    frozen = datetime.now(timezone.utc).isoformat()
    if adopted is None:
        archive_pilot(raw_pilot, pilot_path, archive, frozen)
    try:
        validate_cpu_window(window, cohort_prefix)
    except ValueError:
        decision = {'utc': frozen, **snapshot.metadata(), 'source_commit': snapshot.git_base,
            'feasible': False, 'reason': 'pilot_derived_window_exceeds_registered_cap',
            'pilot_sha256': digest(raw_pilot), 'required_measurement_seconds': window,
            'maximum_measurement_seconds': CPU_MAX_MEASUREMENT_SECONDS, 'comparison_trials_launched': 0}
        exclusive_write(ROOT / 'runs' / (cohort_prefix + '-planning') / 'planning.json',
                        (json.dumps(decision, indent=2) + '\n').encode())
        raise CPUPlanningInfeasible(decision)
    plan, traces = derive_model_plan(pilot, digest(raw_pilot), campaign['trace_directory'],
                                     measurement_seconds=window, provider_url=campaign['inference_url'])
    plan['continuation'] = {'cohort': campaign['cohort'], **snapshot.metadata(), 'frozen_utc': frozen,
        'campaign_descriptor': campaign,
        'pilot_archive': campaign.get('retained_calibration_archive', 'archives/' + cohort_prefix + '-pilot/pilot.json.gz'),
        'trace_directory': campaign['trace_directory'], 'transport_control': campaign['inference_url'] + '/transport-control',
        'emission_lag_p95_limit_seconds': .05, 'transport_latency_p95_limit_seconds': .1,
        'maximum_measurement_seconds': campaign.get('maximum_measurement_seconds', CPU_MAX_MEASUREMENT_SECONDS),
        'measurement_window_rule': 'max(20, ceil(ceil(10 / frozen_low_rate) / 5) * 5); complete pinned calibration only, same window in every condition.',
        'qualification_rule': 'Three passing transport repetitions, all twelve passing model-path controls and four correct warmup answers before all forty-five real-model trials.',
        'resource_scope': 'Shared ' + campaign['host_label'] + ' host; separate CPU affinities do not isolate memory bandwidth, caches or other host services.',
        'resource_envelope': {'cpu_quota_cores_upper_bound': campaign['cpu_quota_cores'],
                              'memory_max_bytes_upper_bound': campaign['inference_memory_bytes'],
                              'memory_swap_max_bytes_upper_bound': campaign['swap_max_bytes']}, 'method_sources': methods}
    if adopted is not None:
        plan['continuation'].update({key: adopted[key] for key in (
            'calibration_source_id', 'collection_source_id', 'calibration_pilot_sha256',
            'calibration_archive_sha256', 'calibration_manifest_sha256', 'source_compatibility')})
    for relative, encoded in traces.items():
        exclusive_write(ROOT / relative, encoded)
    exclusive_write(trace_root / 'plan.json', (json.dumps(plan, indent=2) + '\n').encode())
    replay_cpu_plan(plan)
    exclusive_write(output_path, (json.dumps(plan, indent=2) + '\n').encode())
    return {'source_id': snapshot.source_id, 'plan_path': str(output_path.relative_to(ROOT)),
            'plan_sha256': digest(output_path.read_bytes()), 'pilot_sha256': plan['pilot_sha256'],
            'measurement_seconds': window, 'rates': plan['rates'],
            'qualification_trials': len(plan['blocks']['model-qualification']),
            'real_model_trials': len(plan['blocks']['real-model'])}


def check_campaign(output_path, *, cohort_prefix=None):
    if not __debug__:
        raise RuntimeError('Scientific plan verification requires Python assertions')
    output_path = inside(output_path)
    snapshot = capture(ROOT)
    verify_worktree(ROOT, snapshot.source_id)
    plan = json.loads(output_path.read_text())
    campaign = cpu_campaign_for_plan(plan)
    if cohort_prefix is not None and cohort_prefix != campaign['prefix']:
        raise ValueError('CPU plan belongs to another campaign')
    if output_path != (ROOT / campaign['plan_path']).resolve():
        raise ValueError('CPU plan path differs from its registered campaign')
    if plan['continuation']['source_id'] != snapshot.source_id or plan['continuation']['git_base'] != snapshot.git_base:
        raise ValueError('CPU plan differs from the registered collection source')
    replay_cpu_plan(plan)
    return {'checked': True, 'plan_path': str(output_path.relative_to(ROOT)), 'plan_sha256': digest(output_path.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort-prefix', choices=tuple(CPU_CAMPAIGNS), help='Explicit registered campaign; required for a new freeze')
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--method-file', type=Path, action='append', default=[], help='Retained supervisor/guard source; repeat as needed')
    parser.add_argument('--check', action='store_true', help='Validate an existing frozen plan without changing files')
    args = parser.parse_args()
    if not args.cohort_prefix and (not args.check or args.out is None):
        parser.error('Select --cohort-prefix for a new freeze or provide --out for a read-only check')
    campaign = get_cpu_campaign(args.cohort_prefix) if args.cohort_prefix else None
    output = args.out or Path(campaign['plan_path'])
    pilot = (args.pilot or Path(campaign.get('retained_calibration_pilot',
                 'runs/' + args.cohort_prefix + '-pilot/pilot.json'))) if not args.check else args.pilot
    try:
        result = (check_campaign(output, cohort_prefix=args.cohort_prefix) if args.check else
                  freeze_campaign(pilot, output, args.method_file, cohort_prefix=args.cohort_prefix))
    except CPUPlanningInfeasible as error:
        print(json.dumps(error.record), flush=True)
        raise SystemExit(2)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
