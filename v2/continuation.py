"""Bind the untouched failed prefix to a prospective, reset-per-trial suffix."""
import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from v2.identity import capture, verify_worktree
from v2.plan import ROOT, canonical
from v2.verify import replay_continuation_trial_gate, replay_native_completion_certificate

PARENT_PLAN_SHA256 = 'b1917a52bc70ed7ef83f8d1f071b14de62d9986184f23d4072536dfb7d1117ce'
PARENT_SOURCE_ID = 'sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f'
PARENT_GIT_BASE = 'db31fdad5e022ae165c1661c41632c1c9a761b84'
VERIFIER_SHA256 = '0bdef9bf5c211b093ac30ed47d6714c6a48cb6daa1673c0c41600eca364044bd'
FAILED_TRIAL = 'real-model-high-0-0'
PREFIX_SIZE = 17
TOTAL_SIZE = 45
DEFAULT_RUN_ROOT = 'runs/v2-ollama-fra6s004-cpu-continuation'
METHOD_FILES = (
    'infra/run-fra6s004-continuation.py', 'infra/recover-fra6s004-inference.py',
    'v2/continuation.py', 'scripts/report_cpu_continuation.py', 'PROTOCOL.md',
    'infra/alien6-ollama.service', 'infra/alien6-ollama-proxy.service',
    'infra/alien6-ollama-monitor.service',
)
PROTOCOL = {
    'epoch': 'reset_before_every_new_trial', 'parent_index_base': 0,
    'reset_required': True, 'warmup_responses': 4,
    'refresh_network_repetitions': 3, 'refresh_qualification_trials': 12,
    'original_attempted': 17, 'remaining': 28,
    'global_qualified': False, 'included_for_causal_inference': False,
    'scope': 'Prospective exploratory suffix. Preserve the original failed trial and all prior client outcomes; report epochs separately. Restart effects are not assumed absent.',
}
# Exact retained bytes, including the failed attempt and its unqualified block.
PRIOR_SHA256 = {
    'block.json': '2ade99e2986d76ddb441a46f260313e17d0cb2c5734a24865e32b74b2d172981',
    'manifest.json': '5cfcd607184ec476dea3330449dba239cdc53fc4a23aaa137dd3bddb5e7186e7',
    'real-model-high-0-0.json': 'c11804355194b62b5a91d95ab6cd9e9e40e6117df78af054e3b43e4e5bfb2826',
    'real-model-high-1-1.json': 'a9869ac138b618505c6639c0dfaea72add2802632e70568a82c0b7fd4e4115fe',
    'real-model-high-1-2.json': '275aed96ce02735ec8a68adb8c83d94994833ec7bcbe2695e335d6e4ab6e8c70',
    'real-model-high-2-2.json': '6653f923dcdcf582692c7ca74126a9003c1f6d99c82f0bcb3d781d87cec3ba9f',
    'real-model-high-4-2.json': '90bdd5fcf26649ad67fcf0b1b8b7e7889281c6aeb122dfa67e640813fc0b1d3a',
    'real-model-high-8-0.json': 'ec886fcbd3a4fef9974adb1a670b2f2b47f53282a013452e360d3adace6ca9eb',
    'real-model-low-1-1.json': 'a0ec8de6518664b6b9acd948eabb5f4fcb36202d7301396a1846996f04d7a836',
    'real-model-low-4-1.json': 'f184f48d0334362a2a7410bbab871996512fbc5a2f7851ad594b4b5f799fc46a',
    'real-model-low-4-2.json': '17cdaba53304a4659c46433c47f8406b6263c177afa7891cfa976044aa06df44',
    'real-model-low-8-2.json': '7606e968ec85f1ee2d3d451c47d858fe9c054b4051b1303b0daf3e362ca47689',
    'real-model-near-0-1.json': '41d8ccf3f54e355576cdfe7b6632c24f361f0fd26009361ea36d239bc45f2746',
    'real-model-near-1-2.json': 'db74856f60d90af7b540b0bdad78801097ac913988f84d43a3d8ae14c682764a',
    'real-model-near-2-0.json': 'a9c48a33d1ecc643a5ff286fe219d315b0dbb4f24b42e9a54be5ccae388b31cc',
    'real-model-near-2-1.json': 'c087aa0276e160dd494ee59f4082153127acb9c392e7e8bbad6eb011e32e8604',
    'real-model-near-4-0.json': 'dc5f4b2833c49940f3c887f7c5684607bac3a135fc10c3323b89bca642b1c8d5',
    'real-model-near-4-1.json': 'cd3edb2475b535e03664fa08e8f251d5a6b6d41acd5199e1b23c3710697e1ae4',
    'real-model-near-8-2.json': 'f6bfd1a899e0d862ca806dae3faf23a4137fb0fb92b83371f167f9461001ce2e',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(root, value, *, cli=False):
    """Accept CLI absolute paths inside ROOT; stored references are relative."""
    original = str(value)
    root, value = Path(root).resolve(), Path(value)
    if ('..' in value.parts or (value.is_absolute() and not cli)
            or (not cli and original != value.as_posix())):
        raise ValueError('References must be canonical repository-relative paths')
    path = value if value.is_absolute() else root / value
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError('Path is outside the repository') from None
    if str(relative) == '.' or relative.as_posix() != str(relative):
        raise ValueError('Expected a file or directory inside the repository')
    for current in (path, *path.parents):
        if current == root:
            break
        if current.is_symlink():
            raise ValueError('Study references must not contain symbolic links')
    return path


def record(root, path):
    path = inside(root, path, cli=True)
    raw = path.read_bytes()
    return {'path': path.relative_to(root).as_posix(), 'sha256': digest(raw), 'bytes': len(raw)}


def source_records(root, plan):
    if digest((root / 'v2/verify.py').read_bytes()) != VERIFIER_SHA256:
        raise ValueError('The preserved BBA verifier is required')
    # Replay checks archived collection context and exact bytes. The final
    # publication commit can differ from the execution base; its current HEAD
    # must never be substituted for the base recorded in the observations.
    # The live supervisor separately requires the actual execution HEAD.
    snapshot = capture(root, git_base=PARENT_GIT_BASE)
    if snapshot.source_id != PARENT_SOURCE_ID or snapshot.git_base != PARENT_GIT_BASE:
        raise ValueError('Continuation must retain the exact BBA measurement instrument and Git base')
    verify_worktree(root, snapshot.source_id)
    instrument = [record(root, root / name) for name, _ in snapshot.files]
    methods = set(METHOD_FILES) | {item['path'] for item in plan['continuation']['method_sources']}
    return snapshot.metadata(), instrument, [record(root, inside(root, name)) for name in sorted(methods)]


def inspect_parent(root, plan_path, prior_dir, *, deep=True):
    plan_record = record(root, plan_path)
    if plan_record['sha256'] != PARENT_PLAN_SHA256:
        raise ValueError('The original frozen parent plan changed')
    plan = json.loads(plan_path.read_bytes())
    registration = plan['continuation']
    if registration['source_id'] != PARENT_SOURCE_ID or registration['git_base'] != PARENT_GIT_BASE:
        raise ValueError('Parent study identity differs')
    configs = plan['blocks']['real-model']
    if (len(configs) != TOTAL_SIZE or len({row['trial_id'] for row in configs}) != TOTAL_SIZE
            or configs[PREFIX_SIZE - 1]['trial_id'] != FAILED_TRIAL):
        raise ValueError('Parent order or grid differs from the retained study')
    trace_records = {}
    for rows in plan['blocks'].values():
        for config in rows:
            reference = config['trace_reference']
            path = inside(root, reference['path'])
            item = record(root, path)
            if item['sha256'] != reference['sha256']:
                raise ValueError('A frozen workload changed')
            trace_records[item['path']] = item
    if {p.name for p in prior_dir.iterdir()} != set(PRIOR_SHA256):
        raise ValueError('The complete original prefix, failed trial and block are required')
    files, documents = [], {}
    for name, expected in sorted(PRIOR_SHA256.items()):
        path = inside(root, prior_dir / name, cli=True)
        item = record(root, path)
        if item['sha256'] != expected:
            raise ValueError('Prior evidence changed: ' + name)
        files.append(item)
        if deep or name in ('manifest.json', 'block.json'):
            documents[name] = json.loads(path.read_bytes())
    manifest, block = documents['manifest.json'], documents['block.json']
    if (manifest['source_id'] != PARENT_SOURCE_ID or manifest['git_base'] != PARENT_GIT_BASE
            or manifest['source_commit'] != PARENT_GIT_BASE or manifest['plan_sha256'] != PARENT_PLAN_SHA256):
        raise ValueError('Prior collection identity differs')
    if (block.get('complete') is not False or block.get('qualified') is not False
            or block.get('trials') != PREFIX_SIZE or block.get('invalid') != [FAILED_TRIAL]
            or block.get('reason') != 'native_completion_not_certified'):
        raise ValueError('The original failed block must remain unqualified')
    proxy_identity = {'source_id': PARENT_SOURCE_ID, 'git_base': PARENT_GIT_BASE,
                      'proxy_sha256': registration['source_compatibility']['collection_proxy_sha256']}
    for index, frozen in enumerate(configs[:PREFIX_SIZE] if deep else []):
        document = documents[frozen['trial_id'] + '.json']
        config = document['configuration']
        actual = {key: value for key, value in config.items()
                  if key not in ('trace', 'trace_decoded_sha256', 'worker_addresses')}
        if actual != frozen:
            raise ValueError('Prior trials are not the exact prefix of the frozen plan')
        trace = json.loads(gzip.decompress(inside(root, frozen['trace_reference']['path']).read_bytes()))
        if config['trace'] != trace or config['trace_decoded_sha256'] != digest(canonical(trace)):
            raise ValueError('Prior decoded workload differs from its frozen trace')
        issues = replay_continuation_trial_gate(document, PARENT_SOURCE_ID,
                                                manifest['model_controller_sha256'], PARENT_GIT_BASE)
        certified = replay_native_completion_certificate(config, document['result'],
                                                           document.get('native_completion_certificate'), proxy_identity)
        if issues:
            raise ValueError('Prior collection gates do not reproduce: ' + frozen['trial_id'])
        if index < PREFIX_SIZE - 1:
            if document.get('valid') is not True or document.get('issues') != [] or not certified:
                raise ValueError('One of the sixteen prior valid trials does not reproduce')
        elif (document.get('valid') is not False or document.get('issues') != ['native_completion_not_certified']
              or document.get('native_completion_certificate', {}).get('valid') is not False or certified):
            raise ValueError('The seventeenth failed trial must stay failed')
    prior = {'directory': prior_dir.relative_to(root).as_posix(), 'source_id': PARENT_SOURCE_ID,
             'files': files, 'trials': PREFIX_SIZE, 'valid': PREFIX_SIZE - 1, 'invalid': [FAILED_TRIAL],
             'trial_ids': [row['trial_id'] for row in configs[:PREFIX_SIZE]],
             'validation': 'All sixteen successful trial gates and native certificates replayed; original failed certificate retained unchanged.'}
    return plan, plan_record, prior, list(sorted(trace_records.values(), key=lambda r: r['path']))


def selection_plan(parent, parent_record, manifest_path, created, index):
    value = copy.deepcopy(parent)
    value['blocks'] = {'real-model': [copy.deepcopy(parent['blocks']['real-model'][index])]}
    value['continuation_selection'] = {'parent_plan': parent_record, 'parent_index': index,
        'parent_index_base': 0, 'trial_id': value['blocks']['real-model'][0]['trial_id'],
        'manifest_path': manifest_path, 'created_utc': created,
        'scope': 'Prospective single-trial selection from the original frozen order; not the original 45-trial plan and not a qualified aggregate.'}
    return value


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def prepare_manifest(parent_plan, prior_dir, out, run_root=None, *, root=None):
    root = Path(root or ROOT).resolve()
    parent_path, previous, target = [inside(root, path, cli=True) for path in (parent_plan, prior_dir, out)]
    output_root = inside(root, run_root or DEFAULT_RUN_ROOT, cli=True)
    if target.exists() or (target.parent / 'plans').exists():
        raise FileExistsError('Refusing to overwrite continuation inputs')
    if output_root.exists():
        raise FileExistsError('Continuation results require a fresh output root')
    plan, parent_record, prior, traces = inspect_parent(root, parent_path, previous)
    identity, instrument, methods = source_records(root, plan)
    created = datetime.now(timezone.utc).isoformat()
    manifest = {'schema': 'alien6-research-continuation-v1', 'created_utc': created,
        **identity, 'parent_plan': parent_record, 'prior': prior, 'protocol': copy.deepcopy(PROTOCOL),
        'instrument_files': instrument, 'method_files': methods, 'trace_files': traces,
        'run_root': output_root.relative_to(root).as_posix(),
        'network_path': (output_root / 'network/control.json').relative_to(root).as_posix(),
        'qualification_directory': (output_root / 'qualification').relative_to(root).as_posix(),
        'trials': []}
    prepared = []
    for index in range(PREFIX_SIZE, TOTAL_SIZE):
        config = plan['blocks']['real-model'][index]
        identifier = config['trial_id']
        path = target.parent / 'plans' / (identifier + '.json')
        epoch = output_root / 'trials' / identifier
        raw = encoded(selection_plan(plan, parent_record, target.relative_to(root).as_posix(), created, index))
        manifest['trials'].append({'trial_id': identifier, 'parent_index': index,
            'plan_path': path.relative_to(root).as_posix(), 'plan_sha256': digest(raw),
            'epoch_directory': epoch.relative_to(root).as_posix(),
            'warmup_path': (epoch / 'warmup.json').relative_to(root).as_posix(),
            'recovery_path': (epoch / 'recovery.json').relative_to(root).as_posix(),
            'result_directory': (epoch / 'real-model').relative_to(root).as_posix(),
            'result_path': (epoch / 'real-model' / (identifier + '.json')).relative_to(root).as_posix()})
        prepared.append((path, raw))
    (target.parent / 'plans').mkdir(parents=True, exist_ok=False)
    for path, raw in [*prepared, (target, encoded(manifest))]:
        with path.open('xb') as stream:
            stream.write(raw)
        path.chmod(0o444)
    return manifest


def check_manifest(path, *, root=None, deep=True):
    """Deep replay is the default; fast rechecks still hash all pinned inputs."""
    root = Path(root or ROOT).resolve()
    target = inside(root, path, cli=True)
    manifest = json.loads(target.read_bytes())
    if manifest.get('schema') != 'alien6-research-continuation-v1' or manifest.get('protocol') != PROTOCOL:
        raise ValueError('Continuation protocol changed or claims unsupported qualification')
    created = datetime.fromisoformat(manifest['created_utc'])
    if created.utcoffset() is None:
        raise ValueError('Continuation creation time must include a UTC offset')
    parent_path = inside(root, manifest['parent_plan']['path'])
    prior_dir = inside(root, manifest['prior']['directory'])
    parent, parent_record, prior, traces = inspect_parent(root, parent_path, prior_dir, deep=deep)
    identity, instrument, methods = source_records(root, parent)
    for key, expected in {**identity, 'parent_plan': parent_record, 'prior': prior,
                          'instrument_files': instrument, 'method_files': methods, 'trace_files': traces}.items():
        if manifest.get(key) != expected:
            raise ValueError('Continuation input identity changed: ' + key)
    output = inside(root, manifest['run_root'])
    if (manifest.get('network_path') != (output/'network/control.json').relative_to(root).as_posix()
            or manifest.get('qualification_directory') != (output/'qualification').relative_to(root).as_posix()):
        raise ValueError('Continuation control paths changed')
    trials = manifest.get('trials')
    if not isinstance(trials, list) or len(trials) != TOTAL_SIZE - PREFIX_SIZE:
        raise ValueError('The entire unexecuted suffix is required')
    for index, row in enumerate(trials, start=PREFIX_SIZE):
        identifier = parent['blocks']['real-model'][index]['trial_id']
        if row.get('parent_index') != index or row.get('trial_id') != identifier:
            raise ValueError('Continuation selected, repeated or reordered parent conditions')
        plan_path = inside(root, row['plan_path'])
        expected_path = target.parent/'plans'/(identifier+'.json')
        if plan_path != expected_path:
            raise ValueError('Selected plan path differs from its trial identity')
        raw = plan_path.read_bytes()
        if digest(raw) != row['plan_sha256'] or json.loads(raw) != selection_plan(
                parent, parent_record, target.relative_to(root).as_posix(), manifest['created_utc'], index):
            raise ValueError('A selected plan or exact frozen configuration changed')
        epoch = output/'trials'/identifier
        expected_paths = {'epoch_directory': epoch, 'warmup_path': epoch/'warmup.json',
                          'recovery_path': epoch/'recovery.json', 'result_directory': epoch/'real-model',
                          'result_path': epoch/'real-model'/(identifier+'.json')}
        for key, expected in expected_paths.items():
            if inside(root, row[key]) != expected:
                raise ValueError('Trial output path changed: ' + key)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Freeze a fresh suffix manifest without running inference')
    prepare.add_argument('--parent-plan', required=True, type=Path)
    prepare.add_argument('--prior-dir', required=True, type=Path)
    prepare.add_argument('--out', required=True, type=Path, help='Fresh manifest.json path')
    prepare.add_argument('--run-root', type=Path, default=Path(DEFAULT_RUN_ROOT))
    check = commands.add_parser('check', help='Replay identities and the retained prefix, without inference')
    check.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    document = (prepare_manifest(args.parent_plan, args.prior_dir, args.out, args.run_root)
                if args.command == 'prepare' else check_manifest(args.manifest))
    print(json.dumps({'checked': True, 'source_id': document['source_id'],
                      'prior_trials': document['prior']['trials'], 'remaining_trials': len(document['trials']),
                      'global_qualified': False}))


if __name__ == '__main__':
    main()
