"""Admission lifecycle proofs use fake services, never an instance or model."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from v2.identity import MEASURED_FILES
from v2.admission_study import STUDY_FILES
from v2 import test_inference_recovery as frozen_tests

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('admission_recovery_test', ROOT/'infra/recover-fra6s004-admission.py')
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)
OLD_EPOCH = 'native-inference-proxy:' + '1'*32
NEW_EPOCH = 'native-inference-proxy:' + '2'*32
GIT_BASE = 'e'*40


def source_descriptor():
    registered=json.loads((ROOT/'instruments/manifest.json').read_bytes())['sources'][RECOVERY.BASE_SOURCE_ID]
    files={name:{'sha256':item['sha256'],'bytes':item['bytes']} for name,item in registered['files'].items()}
    for name in STUDY_FILES:
        if name not in files:
            raw=('synthetic source '+name).encode()
            files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    for name in ('infra/recover-fra6s004-inference.py','infra/recover-fra6s004-admission.py'):
        raw=(ROOT/name).read_bytes()
        files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    return {'format':'alien6-collection-source-v1','files':files}


def unit_states(running):
    return {unit:{'ActiveState':'active' if running else 'inactive', 'SubState':'running' if running else 'dead',
        'MainPID':'123' if running else '0','ControlGroup':'/system.slice/'+unit if running else '',
        'KillMode':'control-group','SendSIGKILL':'yes','CPUAccounting':'yes','CPUWeight':'100',
        'User':'alien6-study','DropInPaths':''} for unit in RECOVERY.SERVICES}


def native_state(epoch):
    return {'source_id':RECOVERY.BASE_SOURCE_ID,'git_base':GIT_BASE,'proxy_sha256':RECOVERY.BASE.PROXY_SHA256,
        'clock_domain':epoch,'clock':'time.monotonic','started_total':0,'finished_total':0,
        'uncertain_total':0,'active':[],'records':[]}


class FakeRecovery(RECOVERY.AdmissionRecovery):
    def __init__(self,out,*,reset=True,running=True,previous=OLD_EPOCH,old_active=False,
                 refuse_stop=False,fail_start=False,orphan=False,bad_post=None,fail_post_read=False,
                 fail_pre_admission=False):
        self.test_clock=frozen_tests.Clock()
        self.running,self.restarted=running,False
        self.old_active,self.refuse_stop,self.fail_start,self.orphan=old_active,refuse_stop,fail_start,orphan
        self.bad_post,self.fail_post_read=bad_post,fail_post_read
        self.fail_pre_admission=fail_pre_admission
        self.queries=[]
        self.descriptor=source_descriptor()
        super().__init__(out,RECOVERY.descriptor_id(self.descriptor),reset,previous,
            runner=self.fake_command,clock=self.test_clock.monotonic,sleep=self.test_clock.sleep)

    def environment(self):
        self.record['git_base']=GIT_BASE
        RECOVERY.BASE.GIT_BASE=GIT_BASE
        self.record['study_source_descriptor']=self.descriptor
        self.record['environment']={'service_templates':{unit:self.descriptor['files']['infra/'+unit]['sha256']
            for unit in RECOVERY.SERVICES}, 'source':{'source_id':RECOVERY.BASE_SOURCE_ID,'git_base':GIT_BASE},
            'study_source':{'source_id':self.study_source_id,'git_base':GIT_BASE},
            'source_root':str(RECOVERY.SOURCE_ROOT),
            'limits':{'cpu.max':'800000 100000','memory.max':str(48*1024**3),'memory.swap.max':'0'},
            'ollama_scheduler':{'path':str(RECOVERY.SCHEDULER_PATH),'sha256':RECOVERY.SCHEDULER_SHA256,
                'settings':dict(RECOVERY.SCHEDULER_SETTINGS)}}
        self.record['old_proxy_before']=self.old_proxy()

    def fake_command(self,argv,**kwargs):
        self.test_clock.now+=.01
        if argv==['systemctl','stop','--no-block',*RECOVERY.SERVICES]:
            if not self.refuse_stop:self.running=False
        elif argv==['systemctl','start','--no-block',*RECOVERY.SERVICES]:
            if self.fail_start:return subprocess.CompletedProcess(argv,1,'','controlled start failure')
            self.running,self.restarted=True,True
        else:raise AssertionError('Unexpected command: '+str(argv))
        return subprocess.CompletedProcess(argv,0,'','')

    def units(self,limit=None):
        self.remaining(limit)
        return unit_states(self.running)

    def old_proxy(self,limit=None):
        self.remaining(limit)
        value={'unit':RECOVERY.OLD_PROXY,'state':{'ActiveState':'active' if self.old_active else 'inactive',
            'SubState':'running' if self.old_active else 'dead','MainPID':'99' if self.old_active else '0',
            'ControlGroup':'/system.slice/'+RECOVERY.OLD_PROXY if self.old_active else ''},
            'cgroup':{'path':'/sys/fs/cgroup/system.slice/'+RECOVERY.OLD_PROXY,'exists':self.old_active,
                'populated':int(self.old_active),'recursive_procs':[99] if self.old_active else []}}
        RECOVERY.assert_old_proxy_absent(value)
        return value

    def observe(self,phase,limit=None):
        self.remaining(limit)
        value={'phase':phase,'monotonic':self.clock(),'units':self.units(limit),
            'cgroups':{unit:{'path':'/sys/fs/cgroup/system.slice/'+unit,'exists':self.running,
                'populated':int(self.running),'recursive_procs':[123] if self.running else []}
                for unit in RECOVERY.SERVICES},
            'known_processes':[{'pid':123}] if self.running or self.orphan else [],
            'unavailable_processes':[],'listeners':['127.0.0.1:11435'] if self.running else [],
            'old_proxy':self.old_proxy(limit)}
        self.record['observations'].append(value);self.save()
        return value

    def fake_get(self,url,limit=None):
        self.remaining(limit);self.queries.append(url)
        if not self.running:raise ConnectionRefusedError('controlled stopped state')
        epoch=NEW_EPOCH if self.restarted else OLD_EPOCH
        if '/study-state?' in url:return native_state(epoch)
        if url==RECOVERY.ADMISSION_URL:
            if not self.restarted and self.fail_pre_admission:raise ConnectionError('controlled pre-admission read failure')
            if self.restarted and self.fail_post_read:raise ConnectionError('controlled admission read failure')
            value={'schema':'alien6-shared-admission-state-v1','source_id':self.study_source_id,
                'git_base':GIT_BASE,'base_source_id':RECOVERY.BASE_SOURCE_ID,
                'admission_proxy_sha256':self.descriptor['files']['v2/admission_proxy.py']['sha256'],
                'clock':'time.monotonic','clock_domain':epoch,'mode':'bypass','capacity':1,
                'trial_id':'technical-warmup','configured':False,'configuration_record':None,
                'started_total':0,'finished_total':0,'active':[],'records':[],'occupied':0,
                'quarantined':0,'native_state':native_state(epoch)}
            if self.restarted and self.bad_post:value.update(self.bad_post)
            return value
        if url.endswith('/api/version'):return {'version':'0.40.16'}
        if url.endswith('/api/ps'):return {'models':[]}
        raise AssertionError('Unexpected endpoint: '+url)


class AdmissionRecoveryTests(unittest.TestCase):
    def execute(self,**kwargs):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        recovery=FakeRecovery(Path(temporary.name)/'proof.json',**kwargs)
        with patch.object(RECOVERY.BASE.Recovery,'get',lambda obj,url,limit=None:obj.fake_get(url,limit)):
            code=recovery.execute()
        return code,recovery,json.loads(recovery.out.read_bytes())

    def test_reset_reuses_base_proof_and_proves_both_zero_ledgers_without_generation(self):
        code,recovery,record=self.execute()
        self.assertEqual(code,0)
        self.assertEqual(record['schema'],'alien6-inference-recovery-v1')
        self.assertEqual(record['admission_recovery_schema'],RECOVERY.SCHEMA)
        self.assertEqual(RECOVERY.verify_admission_recovery_record(record,recovery.study_source_id,GIT_BASE),
            {'valid':True,'new_epoch':NEW_EPOCH,'source_id':RECOVERY.BASE_SOURCE_ID,
             'study_source_id':recovery.study_source_id,'generation_requests':0})
        self.assertEqual([row['argv'][1] for row in record['commands']],['stop','start'])
        self.assertEqual(record['post_admission_state']['native_state'],record['post_proxy_state'])
        self.assertTrue(all('/api/generate' not in url and not url.endswith('/start') for url in recovery.queries))
        self.assertLessEqual(record['finished_monotonic']-record['started_monotonic'],120)
        self.assertEqual(record['git_base'],GIT_BASE)  # No hardcoded historical Git context.

    def test_base_helper_module_and_service_globals_remain_untouched(self):
        self.execute()
        self.assertIsNot(RECOVERY.BASE,frozen_tests.RECOVERY)
        self.assertEqual(frozen_tests.RECOVERY.SERVICES,
            ('alien6-ollama-proxy.service','alien6-ollama-monitor.service','alien6-ollama.service'))
        self.assertEqual(hashlib.sha256(RECOVERY.BASE_HELPER.read_bytes()).hexdigest(),RECOVERY.BASE_HELPER_SHA256)

    def test_old_proxy_must_be_absent_before_any_service_mutation(self):
        code,_,record=self.execute(old_active=True)
        self.assertEqual(code,1);self.assertEqual(record['commands'],[])
        self.assertIn('old BBA proxy',record['error']['detail'])

    def test_post_start_admission_mismatch_stops_all_new_services_and_preserves_failure(self):
        for changed in ({'configured':True},{'occupied':1},{'quarantined':1},
                        {'clock_domain':OLD_EPOCH},{'source_id':'sha256:'+'9'*64},
                        {'capacity':True},{'native_state':{}},{'records':[{'sequence':1}]}):
            with self.subTest(changed=changed):
                code,_,record=self.execute(bad_post=changed)
                self.assertEqual(code,1)
                self.assertEqual([row['argv'][1] for row in record['commands']],['stop','start','stop'])
                RECOVERY.BASE.assert_terminated(record['failure_cleanup'])
                self.assertFalse(record['valid'])
                self.assertLessEqual(record['finished_monotonic']-record['started_monotonic'],120)

    def test_post_start_read_failures_use_same_budget_and_cleanup_before_return(self):
        code,_,record=self.execute(fail_post_read=True)
        self.assertEqual(code,1)
        self.assertEqual([row['argv'][1] for row in record['commands']],['stop','start','stop'])
        RECOVERY.BASE.assert_terminated(record['failure_cleanup'])
        self.assertLessEqual(record['finished_monotonic']-record['started_monotonic'],120)
        self.assertLess(record['finished_monotonic']-record['started_monotonic'],5)

    def test_orphan_or_unproven_stop_never_restarts_services(self):
        for kwargs in ({'orphan':True},{'refuse_stop':True}):
            with self.subTest(kwargs=kwargs):
                code,_,record=self.execute(**kwargs)
                self.assertEqual(code,1)
                self.assertTrue(all(row['argv'][1]=='stop' for row in record['commands']))
                self.assertLessEqual(record['finished_monotonic']-record['started_monotonic'],120)

    def test_check_only_has_no_mutations_and_is_not_a_restart_proof(self):
        code,_,record=self.execute(reset=False,running=False)
        self.assertEqual(code,0);self.assertEqual(record['commands'],[])
        self.assertEqual(record['status'],'checked')
        with self.assertRaises(RECOVERY.RecoveryFailure):RECOVERY.validate_proof(record)

    def test_a_supplied_epoch_mismatch_stops_before_mutation(self):
        code,_,record=self.execute(previous='native-inference-proxy:'+'9'*32)
        self.assertEqual(code,1);self.assertEqual(record['commands'],[])

    def test_unverified_live_admission_identity_is_not_treated_as_a_stopped_server(self):
        code,_,record=self.execute(fail_pre_admission=True)
        self.assertEqual(code,1);self.assertEqual(record['commands'],[])
        self.assertIn('admission identity could not be verified',record['error']['detail'])

    def test_live_source_mismatch_aborts_before_base_environment_or_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            runner=Mock()
            recovery=RECOVERY.AdmissionRecovery(Path(directory)/'proof.json','sha256:'+'1'*64,runner=runner)
            with patch('v2.identity.capture',return_value=SimpleNamespace(source_id='sha256:'+'2'*64)), \
                    patch.object(RECOVERY.BASE.Recovery,'environment') as base_environment:
                with self.assertRaisesRegex(RECOVERY.RecoveryFailure,'explicitly selected study'):
                    recovery.environment()
            runner.assert_not_called();base_environment.assert_not_called()

    def test_pure_validator_rechecks_descriptor_old_proxy_and_admission_evidence(self):
        _,recovery,good=self.execute()
        changes=[lambda r:r['study_source_descriptor']['files']['v2/model.py'].update(sha256='9'*64),
            lambda r:r['post_admission_state'].update(finished_total=1),
            lambda r:r['post_admission_state'].update(configured=True),
            lambda r:r['post_admission_state'].update(clock_domain=OLD_EPOCH),
            lambda r:r['old_proxy_before']['state'].update(MainPID='99'),
            lambda r:r['old_proxy_after']['cgroup'].update(exists=True,populated=1,recursive_procs=[99]),
            lambda r:r['termination_proof']['old_proxy']['state'].update(ActiveState='active'),
            lambda r:r['environment']['service_templates'].update({RECOVERY.SERVICES[0]:'9'*64}),
            lambda r:r['environment']['limits'].update({'cpu.max':'400000 100000'}),
            lambda r:r['post_units'][RECOVERY.SERVICES[0]].update(KillMode='process'),
            lambda r:r['environment']['ollama_scheduler']['settings'].update(OLLAMA_NUM_PARALLEL='8'),
            lambda r:r['environment']['ollama_scheduler'].update(sha256='9'*64),
            lambda r:r.update(admission_recovery_sha256='9'*64),
            lambda r:r.update(admission_postcheck_error={'type':'TimeoutError','detail':'uncertain postcheck'}),
            lambda r:r.update(finished_monotonic=r['started_monotonic']+121)]
        with patch.object(RECOVERY.BASE.subprocess,'run',side_effect=AssertionError('No commands in offline validation')),\
             patch.object(RECOVERY.BASE.urllib.request,'urlopen',side_effect=AssertionError('No network in offline validation')):
            self.assertTrue(RECOVERY.validate_proof(good)['valid'])
            for change in changes:
                changed=copy.deepcopy(good);change(changed)
                with self.subTest(change=change),self.assertRaises(RECOVERY.RecoveryFailure):
                    RECOVERY.verify_admission_recovery_record(changed,recovery.study_source_id,GIT_BASE)

    def test_escaped_admission_module_is_detected_without_unrelated_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            proc=Path(directory)
            for pid,module in [('1','v2.admission_proxy'),('2','unrelated-private-argument')]:
                path=proc/pid;path.mkdir()
                (path/'comm').write_text('python3');(path/'cmdline').write_bytes(('python3\0-m\0'+module+'\0').encode())
                (path/'cgroup').write_text('0::/system.slice/unrelated.service\n')
                (path/'exe').symlink_to('/usr/bin/python3')
            known,unavailable=RECOVERY.known_processes(proc)
            self.assertEqual([row['pid'] for row in known],[1]);self.assertEqual(unavailable,[])
            self.assertEqual(known[0]['module'],'v2.admission_proxy')
            self.assertNotIn('unrelated-private-argument',json.dumps(known))

    def test_existing_output_is_not_replaced_and_source_id_is_required_before_io(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'proof.json';path.write_text('original evidence')
            with patch.object(RECOVERY,'AdmissionRecovery') as constructor:
                with self.assertRaises(FileExistsError):
                    RECOVERY.main(['--check-only','--out',str(path),'--study-source-id','sha256:'+'1'*64])
                with self.assertRaises(SystemExit):
                    RECOVERY.main(['--check-only','--out',str(path),'--study-source-id','invalid'])
                constructor.assert_not_called()
            self.assertEqual(path.read_text(),'original evidence')

    def test_scheduler_reader_retains_only_six_allowed_values_and_complete_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ollama.env'
            other='UNRELATED_SETTING=private-sentinel-not-to-retain\n'
            text=other+''.join(key+'='+value+'\n' for key,value in RECOVERY.SCHEDULER_SETTINGS.items())
            path.write_text(text)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(RECOVERY,'SCHEDULER_SHA256',digest):
                result=RECOVERY.read_scheduler(path)
            self.assertEqual(result,{'path':str(path),'sha256':digest,'settings':RECOVERY.SCHEDULER_SETTINGS})
            self.assertNotIn('UNRELATED_SETTING',json.dumps(result))
            self.assertNotIn('private-sentinel',json.dumps(result))

    def test_scheduler_reader_rejects_changed_missing_duplicate_or_unbound_values(self):
        original=''.join(key+'='+value+'\n' for key,value in RECOVERY.SCHEDULER_SETTINGS.items())
        variants=[original.replace('OLLAMA_NUM_PARALLEL=4','OLLAMA_NUM_PARALLEL=8'),
            original.replace('OLLAMA_MAX_QUEUE=256\n',''),original+'OLLAMA_NUM_PARALLEL=4\n',
            original+'not-an-assignment\n']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ollama.env'
            for text in variants:
                path.write_text(text)
                with patch.object(RECOVERY,'SCHEDULER_SHA256',hashlib.sha256(path.read_bytes()).hexdigest()):
                    with self.subTest(text=text),self.assertRaises(RECOVERY.RecoveryFailure):
                        RECOVERY.read_scheduler(path)
            path.write_text(original)
            with patch.object(RECOVERY,'SCHEDULER_SHA256','9'*64):
                with self.assertRaisesRegex(RECOVERY.RecoveryFailure,'source hash'):
                    RECOVERY.read_scheduler(path)


if __name__=='__main__':unittest.main()
