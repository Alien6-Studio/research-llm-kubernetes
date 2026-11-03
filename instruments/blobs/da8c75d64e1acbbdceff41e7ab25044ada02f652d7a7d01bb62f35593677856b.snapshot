#!/usr/bin/env python3
"""Supervise a registered CPU campaign locally on its designated Linux host."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.observe_study_host import atomic_checkpoint
from v2.cpu_campaigns import CPU_CAMPAIGNS, get_cpu_campaign

CLIENT = "a6-capacity-v2"
INFERENCE = "a6-ollama-study"
DEFAULT_PREFIX = "v2-ollama-fra6s004-cpu-long-window"
METHODS = ("infra/run-dell-cpu-campaign.py", "scripts/freeze_cpu_campaign.py", "scripts/observe_study_host.py", "v2/cpu_campaigns.py")
SERVICES = ("alien6-ollama-proxy.service", "alien6-ollama-monitor.service", "alien6-ollama.service")
MAX_DURATION_SECONDS = 86400
SHUTDOWN_MARGIN_SECONDS = 900
TRIAL_BUDGET_SECONDS = 900 + 242 + 180 + 30


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class GateFailure(RuntimeError):
    pass


def campaign_environment(campaign):
    return {"PYTHONDONTWRITEBYTECODE": "1", "STUDY_OLLAMA_URL": campaign["inference_url"],
            "STUDY_OLLAMA_BRIDGE_URL": campaign["inference_url"], "STUDY_INFERENCE_ORIGIN": campaign["inference_url"],
            "STUDY_INFERENCE_MONITOR": campaign["monitor_url"]}


def campaign_methods(campaign):
    return METHODS + (("v2/calibration.py", "v2/verify.py") if campaign.get("retained_calibration_pilot") else ())


def phase_commands(image, campaign):
    prefix, plan = campaign["prefix"], campaign["plan_path"]
    pilot = campaign.get("retained_calibration_pilot", f"runs/{prefix}-pilot/pilot.json")
    freeze = ["python3", "scripts/freeze_cpu_campaign.py", "--cohort-prefix", prefix, "--pilot", pilot, "--out", plan]
    for method in campaign_methods(campaign):
        freeze.extend(["--method-file", method])
    return [
        (("calibration", ["python3", "-c", CALIBRATION_CHECK, prefix]) if campaign.get("retained_calibration_pilot") else
         ("pilot", ["python3", "-m", "v2.model", "pilot", "--out", pilot])),
        ("freeze", freeze),
        ("network", ["python3", "-m", "v2.network_probe", "--plan", plan, "--url",
                     campaign["inference_url"] + "/transport-control", "--out", f"runs/{prefix}-network/control.json"]),
        ("qualification", ["python3", "-m", "v2.model", "block", "--plan", plan, "--block", "model-qualification", "--image", image, "--out", f"runs/{prefix}-qualification"]),
        ("warmup", ["python3", "-m", "v2.model", "warmup", "--out", f"runs/{prefix}-warmup/warmup.json"]),
        ("real-model", ["python3", "-m", "v2.model", "block", "--plan", plan, "--block", "real-model", "--image", image, "--out", f"runs/{prefix}-real-model"]),
    ]


def validate_guard(data, now, boot_id, campaign, required_seconds=0, expected_script_sha256=None):
    metadata = data.get("metadata", {})
    samples = data.get("samples", [])
    if (data.get("status") != "running" or data.get("complete") is not False
            or data.get("guard_action") is not None or not samples
            or metadata.get("instance") != INFERENCE or metadata.get("boot_id") != boot_id
            or metadata.get("memory_floor_bytes", 0) < campaign["host_memory_floor_bytes"]
            or metadata.get("host", "").split(".")[0] != campaign["host_label"]):
        raise GateFailure("The outer-host memory guard is not active and qualified for this instance")
    if expected_script_sha256 is not None and metadata.get('script_sha256') != expected_script_sha256:
        raise GateFailure('The active guard source differs from the frozen campaign method')
    age = now - samples[-1]["monotonic"]
    if age < 0 or age > max(20, 4 * metadata["interval_seconds"]):
        raise GateFailure("The outer-host memory guard checkpoint is stale")
    expires = metadata["started_monotonic"] + metadata["max_duration_seconds"]
    if expires - now < required_seconds + SHUTDOWN_MARGIN_SECONDS:
        raise GateFailure("The host guard does not cover the remaining job budget and 900-second shutdown margin")
    return expires


def lxd_command(instance, root, command, campaign):
    if instance not in (CLIENT, INFERENCE):
        raise ValueError("Unexpected study instance")
    result = ["lxc", "exec", "local:" + instance, "--cwd", root]
    for key, value in campaign_environment(campaign).items():
        result.extend(["--env", key + "=" + value])
    return result + ["--", *command]


def phase_budgets(window):
    if type(window) is not int or not 20 <= window <= 335:
        raise GateFailure("The registered CPU measurement window must be between 20 and 335 seconds")
    # Operational caps, not a promise that arbitrary Kubernetes/network faults finish.
    return {"calibration": 300, "pilot": 6000, "freeze": 300,
            "network": 3 * (5 + window + 10) + 180,
            "qualification": 12 * TRIAL_BUDGET_SECONDS + 180,
            "warmup": 4 * 180 + 180,
            "real-model": 45 * TRIAL_BUDGET_SECONDS + 180}


def comparison_budget_seconds(window):
    budgets = phase_budgets(window)
    return sum(budgets[phase] for phase in ("network", "qualification", "warmup", "real-model"))


def systemd_state(command, *, on_failure=None):
    """Retry inspection only; retain every failed read before trying again."""
    fields = ("LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "ExecMainStartTimestampMonotonic")
    argv = [*command, "--property=" + ",".join(fields)]
    failures = []

    def decoded(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")

    for attempt in range(1, 4):
        failure = {"utc": utc_now(), "attempt": attempt, "maximum_attempts": 3,
                   "argv": argv, "returncode": None, "stdout": "", "stderr": "",
                   "timeout_seconds": 10, "timed_out": False}
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            failure.update(returncode=completed.returncode, stdout=decoded(completed.stdout),
                           stderr=decoded(completed.stderr))
            if completed.returncode:
                failure["reason"] = "command_failed"
            else:
                state = dict(line.split("=", 1) for line in failure["stdout"].splitlines() if "=" in line)
                if state.get("LoadState") != "loaded" or any(field not in state for field in fields):
                    failure["reason"] = "unit_state_unavailable"
                else:
                    # Unit failure is a successfully observed state, not a read
                    # failure. phase_finished must reject it without retrying.
                    return state
        except subprocess.TimeoutExpired as error:
            failure.update(reason="inspection_timeout", timed_out=True,
                           stdout=decoded(error.stdout), stderr=decoded(error.stderr))
        except OSError as error:
            failure.update(reason="inspection_os_error", error_type=type(error).__name__, error=str(error))
        failures.append(failure)
        if on_failure is not None:
            on_failure(failure)
        # Standalone callers also retain diagnostics in their service log.
        print(json.dumps({"unit_inspection_failure": failure}, sort_keys=True), file=sys.stderr, flush=True)
        if attempt < 3:
            time.sleep(1)
    raise GateFailure("Cannot inspect the persistent phase unit after three read-only attempts: "
                      + json.dumps(failures, sort_keys=True))


def phase_finished(state):
    retained = state.get("ActiveState") == "active" and state.get("SubState") == "exited"
    if retained:
        if (state.get("Result") != "success" or state.get("ExecMainStatus") != "0"
                or int(state.get("ExecMainStartTimestampMonotonic", "0")) <= 0):
            raise GateFailure("Retained phase did not finish successfully")
        return True
    if state.get("ActiveState") in ("active", "activating", "deactivating", "reloading"):
        return False
    if (state.get("ActiveState") != "inactive" or state.get("Result") != "success"
            or state.get("ExecMainStatus") != "0" or int(state.get("ExecMainStartTimestampMonotonic", "0")) <= 0):
        raise GateFailure("Persistent phase failed: " + json.dumps(state, sort_keys=True))
    return True


SOURCE_CHECK = """
import hashlib,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]); os.chdir(root); sys.path.insert(0,str(root))
from v2.identity import capture,verify_worktree
snapshot=capture(root); verify_worktree(root,snapshot.source_id)
if snapshot.source_id != sys.argv[2] or snapshot.git_base != sys.argv[3]:
    raise RuntimeError('Registered source or Git base differs from the approved campaign')
methods={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sys.argv[4:]}
print(json.dumps({**snapshot.metadata(),'methods':methods}))
"""

CALIBRATION_CHECK = """
import json,sys
from pathlib import Path
from v2.cpu_campaigns import get_cpu_campaign
from v2.identity import capture,verify_worktree
from v2.verify import verify_retained_cpu_calibration
prefix=sys.argv[1]; campaign=get_cpu_campaign(prefix)
snapshot=capture(Path.cwd());verify_worktree(Path.cwd(),snapshot.source_id)
result=verify_retained_cpu_calibration(prefix,snapshot.source_id,snapshot.git_base,
    pilot_path=Path(campaign['retained_calibration_pilot']))
print(json.dumps(result))
"""

PREFLIGHT = """
import hashlib,json,sys,urllib.request
from pathlib import Path
from v2.service import code_hash
root=Path.cwd()
from v2.cpu_campaigns import get_cpu_campaign
campaign=get_cpu_campaign(sys.argv[2]); prefix=campaign['prefix']
if campaign['terminal']: raise RuntimeError('An interrupted historical attempt cannot be relaunched')
targets=[root/campaign['plan_path'],root/campaign['trace_directory'],root/'archives'/(prefix+'-pilot')]
targets += [root/'runs'/(prefix+'-'+suffix) for suffix in ('pilot','planning','network','qualification','warmup','real-model')]
if any(path.exists() for path in targets): raise RuntimeError('Campaign outputs already exist; do not retry or overwrite')
with urllib.request.urlopen('http://127.0.0.1:18082/stats',timeout=5) as response: state=json.load(response)
if state.get('source_id') != sys.argv[1] or state.get('code_sha256') != code_hash() or state.get('model_controller_sha256') != hashlib.sha256((root/'v2/model_service.py').read_bytes()).hexdigest():
    raise RuntimeError('Persistent controller forward does not expose the approved instrument')
(root/'study-logs').mkdir(exist_ok=True)
print(json.dumps({'fresh_outputs':True,'controller_source_id':state['source_id']}))
"""

CHECK_PHASE = """
import json,sys
from pathlib import Path
phase,prefix=sys.argv[1:]
from v2.cpu_campaigns import get_cpu_campaign
campaign=get_cpu_campaign(prefix)
root=Path.cwd()
if phase=='calibration':
    from v2.identity import capture,verify_worktree
    from v2.verify import verify_retained_cpu_calibration
    snapshot=capture(root);verify_worktree(root,snapshot.source_id)
    result=verify_retained_cpu_calibration(prefix,snapshot.source_id,snapshot.git_base,
        pilot_path=Path(campaign['retained_calibration_pilot']))
elif phase=='freeze':
    from scripts.freeze_cpu_campaign import check_campaign
    result=check_campaign(Path(campaign['plan_path']),cohort_prefix=prefix)
    plan=json.loads((root/campaign['plan_path']).read_text())
    result['measurement_seconds']=plan['blocks']['real-model'][0]['measurement_seconds']
elif phase=='pilot':
    from v2.model import validate_pilot
    from v2.verify import verify_cpu_metadata
    data=json.loads((root/'runs'/f'{prefix}-pilot/pilot.json').read_text())
    validate_pilot(data); verify_cpu_metadata(data['metadata'],prefix); result={'valid':True}
elif phase=='network':
    data=json.loads((root/'runs'/f'{prefix}-network/control.json').read_text())
    if data.get('qualified') is not True or len(data.get('trials',[])) != 3 or not all(row.get('passed') is True for row in data['trials']):
        raise RuntimeError('All three transport controls must pass')
    result={'qualified':True,'trials':3}
elif phase=='warmup':
    data=json.loads((root/'runs'/f'{prefix}-warmup/warmup.json').read_text())
    if data.get('valid') is not True or len(data.get('observations',[])) != 4 or not all(row.get('correct') is True for row in data['observations']):
        raise RuntimeError('All four warmup responses must pass')
    result={'valid':True,'responses':4}
else:
    expected=12 if phase=='qualification' else 45
    data=json.loads((root/'runs'/f'{prefix}-{phase}/block.json').read_text())
    if data.get('complete') is not True or data.get('qualified') is not True or data.get('trials') != expected or data.get('invalid') != []:
        raise RuntimeError('The complete registered comparison did not qualify')
    result={'complete':True,'qualified':True,'trials':expected}
print(json.dumps(result))
"""

PARTIAL_BLOCK = """
import json,sys
from datetime import datetime,timezone
from pathlib import Path
phase,prefix=sys.argv[1:]
from v2.cpu_campaigns import get_cpu_campaign
get_cpu_campaign(prefix)
path=Path('runs')/(prefix+'-'+phase); target=path/'block.json'
if path.is_dir() and not target.exists():
    rows=[]
    for item in sorted(path.glob('*.json')):
        data=json.loads(item.read_text())
        if 'configuration' in data and 'result' in data: rows.append(data)
    state={'complete':False,'qualified':False,'trials':len(rows),'invalid':[row['configuration']['trial_id'] for row in rows if row.get('valid') is not True],
           'reason':'Persistent supervisor stopped before block completion; retain all saved trials without a passing-subset inference.',
           'supervisor_finished_utc':datetime.now(timezone.utc).isoformat()}
    with target.open('x') as stream: json.dump(state,stream,indent=2); stream.write(chr(10))
"""


class Supervisor:
    def __init__(self, args):
        self.args = args
        self.campaign = get_cpu_campaign(args.cohort_prefix)
        if self.campaign['terminal']:
            raise GateFailure('An interrupted historical campaign cannot be relaunched')
        self.prefix = self.campaign['prefix']
        args.client_root = args.client_root or self.campaign['client_root']
        args.inference_root = args.inference_root or self.campaign['inference_root']
        self.deadline = time.monotonic() + args.max_duration_seconds
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.interrupted = False
        self.active_unit = None
        self.active_phase = None
        window = self.campaign.get('measurement_seconds', 120)
        self.phase_limits = phase_budgets(window)
        phases = [name for name, _ in phase_commands(args.image, self.campaign)]
        self.budget_until = time.monotonic() + sum(self.phase_limits[name] for name in phases)
        args.state_dir.mkdir(parents=True, exist_ok=False)
        self.checkpoint = args.state_dir / "status.json"
        self.state = {"status": "starting", "complete": False, "started_utc": utc_now(),
                      "source_id": args.source_id, "git_base": args.git_base, "image": args.image,
                      "campaign": self.campaign,
                      "phase_budgets_seconds": {name: self.phase_limits[name] for name in phases},
                      "shutdown_margin_seconds": SHUTDOWN_MARGIN_SECONDS,
                      "budget_assumptions": {"controller_rpc_seconds": 900, "scale_seconds": 242,
                                             "metadata_rpc_seconds": 180, "orchestration_seconds": 30,
                                             "scope": "Conservative phase caps; unbounded driver CLI calls remain subject to phase and global supervisor deadlines. No automatic trial retry."},
                      "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      "scope": "Server-local LXD supervision; no SSH, workstation process, publication or automatic retry.",
                      "guard_checkpoint": str(args.guard_status), "phases": []}
        self.save()

    def save(self):
        self.state["updated_utc"] = utc_now()
        atomic_checkpoint(self.checkpoint, self.state)

    def guard(self):
        if self.interrupted or time.monotonic() >= self.deadline:
            raise GateFailure("Supervisor signal or duration limit reached")
        now = time.monotonic()
        expires = validate_guard(json.loads(self.args.guard_status.read_text()), now, self.boot_id,
                                 self.campaign, max(0, self.budget_until - now),
                                 hashlib.sha256((ROOT/'scripts/observe_study_host.py').read_bytes()).hexdigest())
        self.deadline = min(self.deadline, expires - SHUTDOWN_MARGIN_SECONDS)
        if self.deadline - now < max(0, self.budget_until - now):
            raise GateFailure('Supervisor lifetime is shorter than the prospective job budget and shutdown margin')
        self.state['effective_deadline_monotonic'] = self.deadline

    def capture(self, command):
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if completed.returncode:
            raise GateFailure(completed.stderr.strip() or completed.stdout.strip() or "Local command failed")
        return completed.stdout

    def vm(self, command):
        return lxd_command(CLIENT, self.args.client_root, command, self.campaign)

    def inspect_unit(self, command, context):
        def retain(failure):
            self.state.setdefault("unit_inspection_failures", []).append({**failure, "context": context})
            self.save()
        return systemd_state(command, on_failure=retain)

    def check_sources(self):
        methods = campaign_methods(self.campaign)
        expected_methods = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in methods}
        results = {}
        for instance, root, methods in ((CLIENT, self.args.client_root, methods), (INFERENCE, self.args.inference_root, ())):
            result = json.loads(self.capture(lxd_command(instance, root, ["python3", "-c", SOURCE_CHECK, root, self.args.source_id, self.args.git_base, *methods], self.campaign)))
            if methods and result["methods"] != expected_methods:
                raise GateFailure("VM campaign method sources differ from this supervisor")
            results[instance] = result
        self.state["source_verification"] = results
        self.save()

    def wait_staging(self):
        self.state["status"] = "waiting_for_staging"
        self.save()
        for unit in self.args.stage_unit:
            while True:
                self.guard()
                status = self.inspect_unit(["systemctl", "show", unit], {"stage_unit": unit})
                self.state["staging"] = {"unit": unit, "state": status}
                self.save()
                if phase_finished(status): break
                time.sleep(5)

    def run_phase(self, phase, command):
        if phase == 'pilot':
            self.budget_until = time.monotonic() + 6000
            self.state['pilot_safety_budget_seconds'] = 6000
        self.guard()
        self.check_sources()
        unit = self.prefix + "-" + phase + ".service"
        vm_log = self.args.client_root + "/study-logs/" + self.prefix + "-" + phase + ".log"
        fresh = "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.open('x').close()"
        self.capture(self.vm(["python3", "-c", fresh, vm_log]))
        record = {"phase": phase, "status": "starting", "started_utc": utc_now(), "unit": unit,
                  "command": command, "vm_log": vm_log}
        self.state["phases"].append(record)
        self.state["status"] = phase
        self.active_unit, self.active_phase = unit, phase
        self.save()
        launch = ["systemd-run", "--quiet", "--no-block", "--unit=" + unit,
                  "--property=Type=oneshot", "--property=TimeoutStartSec=" + str(self.phase_limits[phase]),
                  "--property=TimeoutStopSec=60", "--property=KillMode=control-group",
                  "--property=RemainAfterExit=yes", "--property=StandardOutput=append:" + vm_log,
                  "--property=StandardError=inherit", "--working-directory=" + self.args.client_root]
        launch.extend("--setenv=" + key + "=" + value for key, value in campaign_environment(self.campaign).items())
        self.capture(self.vm(launch + ["--", *command]))
        try:
            while True:
                self.guard()
                status = self.inspect_unit(self.vm(["systemctl", "show", unit]), {"phase": phase, "unit": unit})
                record["unit_state"] = status
                self.save()
                if phase_finished(status): break
                time.sleep(5)
            record["gate"] = json.loads(self.capture(self.vm(["python3", "-c", CHECK_PHASE, phase, self.prefix])))
            if phase == 'freeze':
                budget = comparison_budget_seconds(record['gate']['measurement_seconds'])
                self.budget_until = time.monotonic() + budget
                self.state['comparison_safety_budget_seconds'] = budget
                self.guard()
            record["status"] = "passed"
            self.active_unit = None
        except Exception as error:
            record["status"] = "failed"
            record["error"] = {"type": type(error).__name__, "detail": str(error)}
            if phase == 'freeze':
                try:
                    planning = json.loads(self.capture(self.vm(['cat', f'runs/{self.prefix}-planning/planning.json'])))
                    self.state['planning'] = planning
                    (self.args.state_dir/'planning.json').write_text(json.dumps(planning,indent=2)+'\n')
                except Exception:
                    pass
            raise
        finally:
            record["finished_utc"] = utc_now()
            try:
                output = self.capture(self.vm(["cat", vm_log]))
                (self.args.state_dir / (phase + ".log")).write_text(output)
            except Exception as error:
                record["log_copy_error"] = str(error)
            self.save()

    def cleanup(self):
        results = []
        if self.active_unit:
            command = self.vm(["systemctl", "stop", self.active_unit])
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
                results.append({"target": self.active_unit, "returncode": completed.returncode, "stderr": completed.stderr})
            except Exception as error:
                results.append({"target": self.active_unit, "stop_error": str(error)})
        if self.active_phase in ("qualification", "real-model"):
            try: self.capture(self.vm(["python3", "-c", PARTIAL_BLOCK, self.active_phase, self.prefix]))
            except Exception as error: results.append({"partial_block_error": str(error)})
        command = lxd_command(INFERENCE, self.args.inference_root, ["systemctl", "stop", *SERVICES], self.campaign)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
            results.append({"target": INFERENCE, "services": SERVICES, "returncode": completed.returncode, "stderr": completed.stderr})
        except Exception as error: results.append({"cleanup_error": str(error)})
        self.state["cleanup"] = results

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "interrupted", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "interrupted", True))
        result = 1
        try:
            self.wait_staging()
            self.check_sources()
            self.state["preflight"] = json.loads(self.capture(self.vm(["python3", "-c", PREFLIGHT, self.args.source_id, self.prefix])))
            self.save()
            for phase, command in phase_commands(self.args.image, self.campaign):
                self.run_phase(phase, command)
            self.state["status"] = "campaign_complete"
            self.state["complete"] = True
            result = 0
        except Exception as error:
            self.state["status"] = "stopped_without_retry"
            self.state["error"] = {"type": type(error).__name__, "detail": str(error)}
            if self.state.get('planning',{}).get('feasible') is False:
                self.state['status'] = 'planning_infeasible'
        finally:
            try: self.cleanup()
            except Exception as error: self.state["cleanup_error"] = str(error)
            self.state["finished_utc"] = utc_now()
            self.save()
        print(json.dumps({"status": self.state["status"], "complete": self.state["complete"], "checkpoint": str(self.checkpoint)}), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path, help="Fresh outer-host checkpoint/log directory")
    parser.add_argument("--guard-status", required=True, type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--git-base", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cohort-prefix", choices=sorted(CPU_CAMPAIGNS), default=DEFAULT_PREFIX)
    parser.add_argument("--client-root")
    parser.add_argument("--inference-root")
    parser.add_argument("--stage-unit", action="append", default=[], help="Existing outer-host oneshot unit to await; repeat in dependency order")
    parser.add_argument("--max-duration-seconds", type=int, default=MAX_DURATION_SECONDS)
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.source_id) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", args.git_base):
        parser.error("Expected the approved full source identity and Git base")
    if not 0 < args.max_duration_seconds <= MAX_DURATION_SECONDS:
        parser.error("Supervisor duration must be positive and at most twenty-four hours")
    if any(not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", unit) for unit in args.stage_unit):
        parser.error("Staging dependencies must be explicit system service names")
    if any(value and (not Path(value).is_absolute() or ".." in Path(value).parts) for value in (args.client_root, args.inference_root)):
        parser.error("Instance source roots must be canonical absolute paths")
    if not sys.platform.startswith("linux"):
        parser.error("Run under the outer Linux host's persistent system service")
    return Supervisor(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
