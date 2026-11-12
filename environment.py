"""Record aggregate host and study-container conditions without process arguments."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import threading
import time

NODE = 'a6-research-control-plane'


def command(args, timeout=4):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {'command': args, 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'command': args, 'unavailable': type(error).__name__}


def snapshot():
    result = {'utc': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
              'system': platform.system(), 'machine': platform.machine()}
    if platform.system() == 'Darwin':
        result['host_load'] = command(['sysctl', '-n', 'vm.loadavg'])
        result['host_memory'] = command(['vm_stat'])
        result['host_thermal'] = command(['pmset', '-g', 'therm'])
    else:
        for name in ['stat', 'meminfo', 'pressure/cpu', 'pressure/memory']:
            try:
                result['host_' + name.replace('/', '_')] = Path('/proc', name).read_text()
            except OSError as error:
                result['host_' + name.replace('/', '_')] = {'unavailable': type(error).__name__}
    result['node_cgroup'] = command(['docker', 'exec', NODE, 'sh', '-c',
        'cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.events /sys/fs/cgroup/cpu.pressure /sys/fs/cgroup/memory.pressure'])
    return result


def inventory():
    result = {'system': platform.system(), 'machine': platform.machine(), 'release': platform.release(),
              'docker_capacity': command(['docker', 'info', '--format',
                  '{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"architecture":"{{.Architecture}}","version":"{{.ServerVersion}}"}']),
              'node_limits': command(['docker', 'inspect', NODE, '--format',
                  '{"nano_cpus":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}},"memory_swap_bytes":{{.HostConfig.MemorySwap}}}'])}
    if platform.system() == 'Darwin':
        for field in ['hw.model', 'machdep.cpu.brand_string', 'hw.logicalcpu', 'hw.memsize']:
            result[field] = command(['sysctl', '-n', field])
    return result


class EnvironmentRecorder:
    def __init__(self, interval=2):
        self.interval = interval
        self.samples = []
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stopped.is_set():
            started = time.monotonic()
            sample = snapshot()
            sample['sampling_seconds'] = time.monotonic() - started
            self.samples.append(sample)
            self.stopped.wait(max(0, self.interval - sample['sampling_seconds']))

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=15)
        return {'interval_seconds': self.interval, 'sampler_stopped': not self.thread.is_alive(),
                'samples': self.samples, 'limits': 'Aggregate host and cgroup observations do not measure all GPU interference or establish a dedicated physical host.'}
