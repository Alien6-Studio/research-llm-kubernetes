#!/usr/bin/env python3
"""Persistent supervision of the frozen24-arm shared-admission comparison."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.observe_study_host import atomic_checkpoint
from v2.admission_study import check_study, verify_sources
from v2.admission_driver import study_identity
from v2.verify import replay_network_control, verify_standalone_warmup

spec = importlib.util.spec_from_file_location('admission_supervisor_base', ROOT/'infra/run-fra6s004-continuation.py')
base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
base.PREFIX = 'v2-ollama-shared-admission'
PREFIX = base.PREFIX
SERVICES = ('alien6-admission-proxy.service', 'alien6-ollama-monitor.service', 'alien6-ollama.service')


class AdmissionStudy(base.Continuation):
    def __init__(self, args):
        if socket.gethostname().split('.')[0] != 'fra6s004':
            raise RuntimeError('This study may only use the designated FRA host')
        self.args = args
        self.campaign = base.legacy.get_cpu_campaign(base.PARENT_PREFIX)
        self.manifest_path = (ROOT/args.manifest).resolve()
        self.manifest = check_study(self.manifest_path, live=True)
        self.base_identity, snapshot = study_identity(ROOT)
        self.identity = snapshot.metadata()
        self.rows = self.manifest['cases']
        self.budget_until = time.monotonic() + base.prospective_budget(len(self.rows)) + 240
        self.hard_deadline = time.monotonic() + 86400 - base.RESERVE
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.interrupted, self.active = False, None
        args.state_dir.mkdir(parents=True, exist_ok=False)
        self.checkpoint = args.state_dir/'status.json'
        self.state = {'status': 'starting', 'complete': False, 'qualified': False,
            'scope': 'Prospective paired exploratory comparison, independently initialized arms; no library efficacy assumed.',
            'started_utc': base.utc_now(), **self.identity, 'base_source_id': self.base_identity.source_id,
            'manifest_path': str(self.manifest_path.relative_to(ROOT)),
            'manifest_sha256': hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
            'supervisor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'prospective_budget_seconds': base.prospective_budget(len(self.rows)) + 240,
            'shutdown_reserve_seconds': base.RESERVE, 'phase_caps_seconds': base.CAPS,
            'capacity_selection': self.manifest['calibration']['selection'], 'trials': [], 'phases': []}
        self.save()

    def verify_sources(self):
        check_study(self.manifest_path, live=True)
        relative = str(self.manifest_path.relative_to(ROOT))
        code = "from pathlib import Path;from v2.admission_study import check_study;import sys;check_study(Path(sys.argv[1]),live=True);print('verified')"
        for instance in (base.legacy.CLIENT, base.legacy.INFERENCE):
            self.capture(instance, ['python3', '-c', code, relative])

    def recovery(self, name, target):
        self.capture(base.legacy.INFERENCE, ['python3', '-c',
            "from pathlib import Path;import sys;Path(sys.argv[1]).parent.mkdir(parents=True,exist_ok=True)", target])
        self.phase(name, base.legacy.INFERENCE, ['python3', 'infra/recover-fra6s004-admission.py',
            '--reset', '--out', target, '--previous-epoch', self.previous_epoch,
            '--study-source-id', self.identity['source_id']], base.CAPS['recovery'])
        raw = self.capture(base.legacy.INFERENCE, ['cat', target])
        proof = json.loads(raw)
        check = "import importlib.util,json,sys;from pathlib import Path;p=Path('infra/recover-fra6s004-admission.py');s=importlib.util.spec_from_file_location('reset',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.validate_proof(json.loads(Path(sys.argv[1]).read_text()));print('verified')"
        self.capture(base.legacy.INFERENCE, ['python3', '-c', check, target])
        self.write_vm(target, raw)
        self.previous_epoch = proof['new_epoch']
        return proof

    def cleanup(self):
        outcomes = []
        if self.active:
            instance, unit = self.active
            try:
                result = subprocess.run(self.argv(instance, ['systemctl', 'stop', unit]),
                    capture_output=True, text=True, timeout=45)
                outcomes.append({'unit': unit, 'returncode': result.returncode, 'stderr': result.stderr})
            except Exception as error:
                outcomes.append({'unit': unit, 'error': str(error)})
        try:
            # The legacy study proxy must remain off; it shares the private port.
            result = subprocess.run(self.argv(base.legacy.INFERENCE, ['systemctl', 'stop',
                *SERVICES, 'alien6-ollama-proxy.service']), capture_output=True, text=True, timeout=100)
            outcomes.append({'instance': base.legacy.INFERENCE, 'returncode': result.returncode, 'stderr': result.stderr})
        except Exception as error:
            outcomes.append({'instance': base.legacy.INFERENCE, 'error': str(error)})
        self.state['cleanup'] = outcomes

    def bind_controls(self):
        """Bind unchanged BBA collectors to this study and its fresh proxy epoch."""
        run_root = self.manifest['run_root']
        program = """
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);paths=[root/'network/control.json',root/'initial-recovery.json']
paths += sorted((root/'qualification').glob('*.json'))
if len(paths)!=16: raise RuntimeError('All3+12 controls and their manifests must exist')
print(json.dumps([{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in paths]))
"""
        files = json.loads(self.capture(base.legacy.CLIENT, ['python3', '-c', program, run_root]))
        query = "from v2.run import json_http;import json;print(json.dumps(json_http('http://172.30.64.3:11437/study-admission/state?after=0&records=1',timeout=5)))"
        state = json.loads(self.capture(base.legacy.CLIENT, ['python3', '-c', query]))
        native = state['native_state']
        if (state.get('source_id') != self.identity['source_id'] or state.get('clock_domain') != self.previous_epoch
                or state.get('configured') is not False or state.get('active') != []
                or state.get('occupied') != 0 or state.get('quarantined') != 0
                or state.get('started_total') != 0 or state.get('finished_total') != 0
                or native.get('started_total') != 0 or native.get('finished_total') != 0 or native.get('uncertain_total') != 0):
            raise RuntimeError('Control transport was not bracketed by the unchanged empty study proxy epoch')
        binding = {'schema': 'alien6-admission-control-binding-v1', **self.identity,
            'base_source_id': self.base_identity.source_id, 'manifest_sha256': self.state['manifest_sha256'],
            'created_utc': base.utc_now(), 'files': files, 'after_controls_admission_state': state,
            'scope': 'Fresh unchanged-base transport/generator controls bracketed by this extended proxy epoch before all comparison arms.'}
        raw = json.dumps(binding, indent=2) + '\n'
        target = run_root + '/study-control-binding.json'
        self.write_vm(target, raw)
        self.state['control_binding'] = {'path': target, 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
        self.save()

    def export(self):
        run_root = self.manifest['run_root']
        folder = ROOT/'archives'/PREFIX; folder.mkdir(parents=True, exist_ok=False)
        guest = '/tmp/' + PREFIX + '-collection.tar.gz'
        result = subprocess.run(self.argv(base.legacy.CLIENT, ['tar', '-C', self.campaign['client_root'],
            '-czf', guest, '--', run_root]), capture_output=True, text=True, timeout=300)
        if result.returncode: raise RuntimeError('Raw collection export failed: ' + result.stderr)
        archive = folder/'collection.tar.gz'
        result = subprocess.run(['lxc', 'file', 'pull', 'local:' + base.legacy.CLIENT + guest, str(archive)],
            capture_output=True, text=True, timeout=180)
        if result.returncode: raise RuntimeError('Raw collection copy failed: ' + result.stderr)
        if (ROOT/run_root).exists(): raise FileExistsError('Never overwrite an exported comparison')
        with tarfile.open(archive, 'r:gz') as stream:
            for item in stream.getmembers():
                path = Path(item.name)
                if (path.is_absolute() or '..' in path.parts or not path.is_relative_to(run_root)
                        or not (item.isfile() or item.isdir())):
                    raise ValueError('Unexpected archive member')
            stream.extractall(ROOT, filter='data')
        records = []
        for path in sorted((ROOT/run_root).rglob('*')):
            if path.is_file():
                raw = path.read_bytes()
                records.append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        (folder/'collection-manifest.json').write_text(json.dumps({'files': records, 'archive_sha256': sha,
            'exported_utc': base.utc_now()}, indent=2) + '\n')
        self.state['preservation'] = {'archive': str(archive.relative_to(ROOT)), 'archive_sha256': sha, 'files': len(records)}
        result = subprocess.run(['python3', '-m', 'v2.admission_study', 'report', '--manifest',
            str(self.manifest_path.relative_to(ROOT)), '--out', 'analysis/' + PREFIX],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        self.state['offline_report'] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        if result.returncode: raise RuntimeError('Offline paired analysis failed; raw observations are preserved')

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'interrupted', True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, 'interrupted', True))
        code = 1
        try:
            self.guard(); self.verify_sources()
            self.previous_epoch = self.args.previous_epoch
            run_root = self.manifest['run_root']
            self.recovery('initial-recovery', run_root + '/initial-recovery.json')
            parent = self.manifest['parent_plan']['path']
            self.phase('network', base.legacy.CLIENT, ['python3', '-m', 'v2.network_probe', '--plan', parent,
                '--url', self.campaign['inference_url'] + '/transport-control', '--out', run_root + '/network/control.json'], base.CAPS['network'])
            network = self.read_vm(run_root + '/network/control.json')
            if network.get('qualified') is not True or len(network.get('trials', [])) != 3 or not replay_network_control(network):
                raise RuntimeError('All three fresh transport controls must pass')
            self.phase('qualification', base.legacy.CLIENT, ['python3', '-m', 'v2.model', 'block', '--plan', parent,
                '--block', 'model-qualification', '--image', self.args.image, '--out', run_root + '/qualification'], base.CAPS['qualification'])
            if self.read_vm(run_root + '/qualification/block.json') != {'complete': True, 'qualified': True, 'trials': 12, 'invalid': []}:
                raise RuntimeError('The complete fresh generator qualification failed')
            self.bind_controls()
            for row in self.rows:
                self.verify_sources()
                remaining = len(self.rows) - len(self.state['trials'])
                self.budget_until = time.monotonic() + remaining * sum(base.CAPS[n] for n in ('recovery', 'warmup', 'trial', 'inspection'))
                if not self.state['trials']:
                    self.budget_until += 180  # First real arm is replayed before arm two.
                self.guard()
                identifier = row['case_id']
                self.recovery('recovery-' + identifier, row['recovery_path'])
                self.phase('warmup-' + identifier, base.legacy.CLIENT, ['python3', '-m', 'v2.model', 'warmup',
                    '--out', row['warmup_path']], base.CAPS['warmup'])
                warm = self.read_vm(row['warmup_path'])
                if warm.get('valid') is not True or verify_standalone_warmup(warm) != 4:
                    raise RuntimeError('Identical four-response technical warmup failed')
                self.phase('trial-' + identifier, base.legacy.CLIENT, ['python3', '-m', 'v2.admission_driver',
                    '--plan', row['plan_path'], '--out', row['result_directory'], '--image', self.args.image], base.CAPS['trial'])
                document = self.read_vm(row['result_path'])
                if document.get('capture_valid') is not True:
                    raise RuntimeError('Technical capture failed; the arm will not be repeated')
                self.state['trials'].append({'case_id': identifier, 'pair_id': row['pair_id'],
                    'mode': row['mode'], 'capacity': row['capacity'], 'capture_valid': True,
                    'resource_qualified': document['resource_qualified'], 'result_path': row['result_path'],
                    'native_issues': document['native_issues'], 'recorded_utc': base.utc_now()})
                self.save()
                if len(self.state['trials']) == 1:
                    self.phase('first-arm-offline-replay', base.legacy.CLIENT,
                        ['python3', '-m', 'v2.admission_study', 'report', '--manifest',
                         str(self.manifest_path.relative_to(ROOT)), '--out', run_root + '/first-arm-replay'], 180)
            self.state.update(status='paired_exploratory_collection_complete', complete=True,
                primary_capture_valid_trials=len(self.state['trials']),
                resource_complete_trials=sum(row['resource_qualified'] for row in self.state['trials']))
            code = 0
        except Exception as error:
            self.state.update(status='stopped_without_retry', error={'type': type(error).__name__, 'detail': str(error)})
        finally:
            self.cleanup(); self.state['finished_utc'] = base.utc_now(); self.save()
            try:
                raw = json.dumps(self.state, indent=2) + '\n'
                result = subprocess.run(self.argv(base.legacy.CLIENT, ['python3', '-c',
                    "from pathlib import Path;import sys;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.open('x').write(sys.stdin.read())",
                    self.manifest['run_root'] + '/supervisor-final.json']), input=raw, capture_output=True, text=True, timeout=30)
                if result.returncode: self.state['final_checkpoint_copy_error'] = result.stderr
                self.export()
            except Exception as error:
                self.state['preservation_error'] = {'type': type(error).__name__, 'detail': str(error)}; code = 1
            self.save()
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--guard-status', type=Path, required=True)
    parser.add_argument('--previous-epoch', required=True)
    parser.add_argument('--image', default='alien6-capacity:fra6s004-cpu-bba1acee')
    raise SystemExit(AdmissionStudy(parser.parse_args()).run())


if __name__ == '__main__':
    main()
