"""Explicit CPU campaign identities, outside the frozen measurement instruments."""
from types import MappingProxyType


_CAMPAIGNS = {
    'v2-ollama-dell-cpu': {
        'prefix': 'v2-ollama-dell-cpu', 'cohort': 'ollama-dell-cpu', 'host_label': 'Dell',
        'plan_path': 'protocol/ollama-dell-cpu-plan.json', 'trace_directory': 'protocol/ollama-dell-cpu',
        'inference_url': 'http://172.16.0.244:11437', 'monitor_url': 'http://172.16.0.244:11436',
        'client_address': None, 'inference_address': '172.16.0.244',
        'client_root': '/opt/alien6-dell-cpu', 'inference_root': '/opt/alien6-research',
        'client_instance': 'a6-capacity-v2', 'inference_instance': 'a6-ollama-study',
        'cpu_quota_cores': 8, 'inference_memory_bytes': 28 * 1024 ** 3, 'swap_max_bytes': 0,
        'host_memory_floor_bytes': 8 * 1024 ** 3, 'terminal': True,
    },
    'v2-ollama-fra6s004-cpu': {
        'prefix': 'v2-ollama-fra6s004-cpu', 'cohort': 'ollama-fra6s004-cpu', 'host_label': 'fra6s004',
        'plan_path': 'protocol/ollama-fra6s004-cpu-plan.json', 'trace_directory': 'protocol/ollama-fra6s004-cpu',
        'inference_url': 'http://172.30.64.3:11437', 'monitor_url': 'http://172.30.64.3:11436',
        'client_address': '172.30.64.2', 'inference_address': '172.30.64.3',
        'client_root': '/opt/alien6-fra6s004-cpu', 'inference_root': '/opt/alien6-research',
        'client_instance': 'a6-capacity-v2', 'inference_instance': 'a6-ollama-study',
        'cpu_quota_cores': 8, 'inference_memory_bytes': 48 * 1024 ** 3, 'swap_max_bytes': 0,
        'host_memory_floor_bytes': 16 * 1024 ** 3, 'terminal': True,
    },
    'v2-ollama-fra6s004-cpu-confirmation': {
        'prefix': 'v2-ollama-fra6s004-cpu-confirmation', 'cohort': 'ollama-fra6s004-cpu-confirmation', 'host_label': 'fra6s004',
        'plan_path': 'protocol/ollama-fra6s004-cpu-confirmation-plan.json',
        'trace_directory': 'protocol/ollama-fra6s004-cpu-confirmation',
        'inference_url': 'http://172.30.64.3:11437', 'monitor_url': 'http://172.30.64.3:11436',
        'client_address': '172.30.64.2', 'inference_address': '172.30.64.3',
        'client_root': '/opt/alien6-fra6s004-cpu', 'inference_root': '/opt/alien6-research',
        'client_instance': 'a6-capacity-v2', 'inference_instance': 'a6-ollama-study',
        'cpu_quota_cores': 8, 'inference_memory_bytes': 48 * 1024 ** 3, 'swap_max_bytes': 0,
        'host_memory_floor_bytes': 16 * 1024 ** 3, 'terminal': True,
    },
    'v2-ollama-fra6s004-cpu-long-window': {
        'prefix': 'v2-ollama-fra6s004-cpu-long-window', 'cohort': 'ollama-fra6s004-cpu-long-window', 'host_label': 'fra6s004',
        'plan_path': 'protocol/ollama-fra6s004-cpu-long-window-plan.json',
        'trace_directory': 'protocol/ollama-fra6s004-cpu-long-window',
        'inference_url': 'http://172.30.64.3:11437', 'monitor_url': 'http://172.30.64.3:11436',
        'client_address': '172.30.64.2', 'inference_address': '172.30.64.3',
        'client_root': '/opt/alien6-fra6s004-cpu', 'inference_root': '/opt/alien6-research',
        'client_instance': 'a6-capacity-v2', 'inference_instance': 'a6-ollama-study',
        'cpu_quota_cores': 8, 'inference_memory_bytes': 48 * 1024 ** 3, 'swap_max_bytes': 0,
        'host_memory_floor_bytes': 16 * 1024 ** 3, 'terminal': False,
        'retained_calibration_pilot': 'runs/v2-ollama-fra6s004-cpu-confirmation-pilot/pilot.json',
        'retained_calibration_archive': 'archives/v2-ollama-fra6s004-cpu-confirmation-pilot/pilot.json.gz',
        'calibration_source_id': 'sha256:c811876fbf14c2af54a18f600044368dd0a92561497e0bbdf8d02c2a6bd86c3b',
        'collection_source_id': 'sha256:bba1acee1beb772ccb9d45a18bb3e30c88dc5e05f53e7649d918db4d26afdd3f',
        'calibration_pilot_sha256': '1f081e1a6174a7dcdbe479b06f7df4b18661bcd811bb0f8ac876b64a4bf10224',
        'calibration_archive_sha256': '0de3f7de6a5e5bf32e167de8d8e9c28be27ffa5e1770906da4aeed85f7e0e64f',
        'calibration_manifest_sha256': '38bfd44604b2ee5485a123cdec80caa6f8ea38296238fe130f784e8f752f94a7',
        'measurement_seconds': 335, 'maximum_measurement_seconds': 335,
        'maximum_duration_seconds': 86400,
    },
}
CPU_CAMPAIGNS = MappingProxyType({prefix: MappingProxyType(data) for prefix, data in _CAMPAIGNS.items()})


def get_cpu_campaign(prefix):
    """Return a caller-owned copy; unregistered attempts cannot borrow a study."""
    try:
        return dict(CPU_CAMPAIGNS[prefix])
    except KeyError as error:
        raise ValueError('CPU campaign prefix is not explicitly registered') from error


def cpu_campaign_for_plan(plan):
    cohort = plan.get('continuation', {}).get('cohort')
    matches = [prefix for prefix, data in CPU_CAMPAIGNS.items() if data['cohort'] == cohort]
    if len(matches) != 1:
        raise ValueError('CPU plan does not identify exactly one registered campaign')
    return get_cpu_campaign(matches[0])
