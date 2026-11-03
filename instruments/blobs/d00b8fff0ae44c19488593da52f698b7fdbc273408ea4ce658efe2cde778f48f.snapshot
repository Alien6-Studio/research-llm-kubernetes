"""Replay raw V2 outcomes; trials, rather than requests, are statistical units."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

from v2.run import percentile, useful


def quantiles(values):
    values = list(values)
    return {'n': len(values), **{f'p{int(p * 100)}': percentile(values, p) for p in [.5, .95, .99]}}


def fields(text):
    return {key: int(value) for key, value in (line.split() for line in text.splitlines())}


def resources(samples):
    groups = defaultdict(list)
    for sample in samples:
        for role in [sample['controller'], sample['provider'], *sample['workers']]:
            if 'uid' in role:
                groups[(role['role'], role['uid'])].append(role)
    result = []
    for (role, uid), values in groups.items():
        values.sort(key=lambda r: r['at'])
        a, b = values[0], values[-1]
        memory = [(r['at'], int(r['memory.current'])) for r in values if r.get('memory.current')]
        byte_seconds = sum((t1 - t0) * (m0 + m1) / 2 for (t0, m0), (t1, m1) in zip(memory, memory[1:]))
        sa, sb = fields(a.get('cpu.stat') or ''), fields(b.get('cpu.stat') or '')
        result.append({'role': role, 'uid': uid, 'samples': len(values),
                       'observed_seconds': b['at'] - a['at'], 'cpu_seconds': b['cpu_seconds'] - a['cpu_seconds'],
                       'cgroup_cpu_seconds': (sb['usage_usec'] - sa['usage_usec']) / 1e6 if 'usage_usec' in sa and 'usage_usec' in sb else None,
                       'throttled_seconds': (sb['throttled_usec'] - sa['throttled_usec']) / 1e6 if 'throttled_usec' in sa and 'throttled_usec' in sb else None,
                       'memory_byte_seconds': byte_seconds if len(memory) >= 2 else None,
                       'memory_peak_observed_bytes': max((m for _, m in memory), default=None),
                       'max_sample_gap_seconds': max((right['at'] - left['at'] for left, right in zip(values, values[1:])), default=None)})
    return result


def summarize(document):
    config, result = document['configuration'], document['result']
    planned = {r['id']: r for r in config['trace']}
    rows = result['requests']
    assert len(rows) == len(planned) and {r['id'] for r in rows} == set(planned)
    for row in rows:
        assert abs(row['scheduled'] - result['start'] - planned[row['id']]['offset']) < 1e-7
        assert abs(row['finished'] - row['scheduled'] - row['latency_seconds']) < 1e-9
    warmup, duration = config['warmup_seconds'], config['measurement_seconds']
    measured = [r for r in rows if warmup <= planned[r['id']]['offset'] < warmup + duration]
    emitted = [r for r in measured if r['emitted'] is not None]
    completed = [r for r in measured if useful(r, config)]
    ids = {r['id'] for r in measured}
    window = [r for r in rows if warmup <= r['finished'] - result['start'] < warmup + duration]
    provider_archive = result['role_records']['provider']
    provider = provider_archive['records']
    consumed = [r for r in provider if r['allowed']]
    ledger = provider_archive['ledger']
    events = [e for e in ledger['events'] if e['action'] == 'consumed']
    assert len(consumed) == len(events)
    assert sum(r['units'] for r in consumed) == sum(e['units'] for e in events)
    assert {r['reservation'] for r in consumed} == {e['reservation'] for e in events}
    assert len({r['id'] for r in consumed}) == len(consumed), 'More than one inference consumed for a primary request'
    if not config.get('direct_ollama'):
        successful = {r['id'] for r in rows if r['status'] == 200 and r.get('correct')}
        assert successful <= {r['id'] for r in consumed}, 'Successful client outcome without consumed inference'
    for values in ledger['windows'].values():
        assert values['consumed_units'] <= ledger['unit_limit']
        assert values['consumed_requests'] <= ledger['request_limit']
    worker_rows = [r for key, archive in result['role_records'].items() if key != 'provider' for r in archive['records']]
    workers = [r for r in worker_rows if r['id'] in ids]
    stage_names = {'queue': ('queue_entered', 'queue_left'), 'admission': ('admission_started', 'admission_finished'),
                   'provider_http': ('provider_call_started', 'provider_call_finished')}
    spans = {name: quantiles(r[end] - r[start] for r in workers if r.get(start) is not None and r.get(end) is not None)
             for name, (start, end) in stage_names.items()}
    complete_windows = []
    if provider:
        lower = math.ceil(min(r['provider_received'] for r in provider))
        upper = math.floor(max(r['provider_finished'] for r in provider))
        for number in range(lower, upper):
            used = ledger['windows'].get(str(number), ledger['windows'].get(number, {})).get('consumed_units', 0)
            complete_windows.append({'window': number, 'units': used, 'utilization': used / ledger['unit_limit']})
    physical = resources(result['resource_samples'])
    total_useful = sum(useful(r, config) for r in rows)
    model_rows = []
    if config.get('direct_ollama'):
        for row in rows:
            raw = row.get('response', {}).get('inference') or row.get('late_response', {}).get('inference')
            if raw: model_rows.append({'id': row['id'], 'raw': raw})
    elif config['provider']['mode'] == 'ollama':
        model_rows = [{'id': row['id'], 'raw': row['inference']} for row in consumed if row.get('inference')]
    measured_model = [row for row in model_rows if row['id'] in ids]
    model = {'observed_responses': len(measured_model)}
    for key in ['prompt_eval_count', 'prompt_eval_cached_count', 'eval_count']:
        known = [r['raw'][key] for r in measured_model if key in r['raw']]
        model[key] = {'sum_observed': sum(known), 'known_responses': len(known), 'unknown_responses': len(measured_model) - len(known)}
    for key in ['load_duration', 'prompt_eval_duration', 'eval_duration', 'total_duration']:
        model[key + '_seconds'] = quantiles(r['raw'][key] / 1e9 for r in measured_model if key in r['raw'])
    residuals = []
    for row in measured_model:
        raw = row['raw']
        keys = ['total_duration', 'load_duration', 'prompt_eval_duration', 'eval_duration']
        if all(key in raw for key in keys):
            residuals.append((raw['total_duration'] - raw['load_duration'] - raw['prompt_eval_duration'] - raw['eval_duration']) / 1e9)
    model['unattributed_server_residual_seconds'] = quantiles(residuals)
    model['timing_limit'] = 'API-reported durations; the residual is not identified as queue waiting. Missing token counts remain unknown.'
    return {'trial_id': config['trial_id'], 'block': config['block'], 'replicas': config['replicas'],
            'path': 'direct-model' if config.get('direct_ollama') else 'direct-provider' if config.get('direct_provider') else 'workers',
            'profile': config['profile'], 'policy': config['policy'], 'repetition': config['repetition'],
            'node_cpus': config['node_cpus'], 'quota': config['provider']['tokens_per_second'],
            'valid': document['valid'], 'issues': document['issues'], 'offered': len(measured), 'emitted': len(emitted),
            'worker_accepted': sum(r.get('reason') != 'worker_queue_full' for r in workers),
            'provider_consumed': sum(r['id'] in ids for r in consumed),
            'useful': len(completed), 'useful_fraction': len(completed) / max(1, len(measured)),
            'offered_per_second': len(measured) / duration, 'emitted_per_second': len(emitted) / duration,
            'useful_cohort_per_second': len(completed) / duration,
            'useful_window_per_second': sum(useful(r, config) for r in window) / duration,
            'status_counts': dict(Counter(str(r['status']) for r in measured)),
            'failure_reasons': dict(Counter(r.get('reason') or r.get('response', {}).get('reason') or 'unspecified'
                                            for r in measured if not useful(r, config))),
            'client_expirations': sum(r.get('reason') == 'client_deadline' or r['status'] == 504 for r in measured),
            'all_terminal_latency_seconds': quantiles(r['latency_seconds'] for r in measured),
            'emitted_terminal_latency_seconds': quantiles(r['latency_seconds'] for r in emitted),
            'useful_latency_seconds': quantiles(r['latency_seconds'] for r in completed),
            'emission_lag_seconds': quantiles(r['emitted'] - r['scheduled'] for r in rows if r['emitted'] is not None),
            'stage_seconds': spans,
            'worker_concurrency_max_sampled': max((sum(w.get('active', 0) for w in s['workers']) for s in result['resource_samples']), default=None),
            'worker_queue_max_sampled': max((sum(w.get('queued', 0) for w in s['workers']) for s in result['resource_samples']), default=None),
            'provider_complete_windows': complete_windows,
            'ledger_event_counts': dict(Counter(e['action'] for e in ledger['events'])),
            'provider_http_refusals': sum(r['id'] in ids and r['status'] in [409, 429] for r in provider),
            'resources': physical, 'whole_trace_useful': total_useful,
            'model': model,
            'observed_role_cpu_seconds_per_whole_trace_useful': sum(r['cpu_seconds'] for r in physical) / total_useful if total_useful else None,
            'resource_accounting_scope': 'Observed first-to-last role sample spans, including warmup and bounded drain; shared node overhead is separate.'}


def interval(values):
    values = list(values)
    n = len(values)
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if n > 1 else None
    half = 4.302652729911275 * sd / math.sqrt(n) if n == 3 else None
    return {'values': values, 'n': n, 'mean': mean, 'sd': sd,
            'descriptive_95pct_t_interval': [mean - half, mean + half] if half is not None else None,
            'assumption': 'Three independent trial-level repetitions, normal paired effects; normality is not established by n=3. No multiplicity-adjusted confirmatory test.'}


def planning(sd, effect):
    if sd is None: return None
    upper_sd = sd * math.sqrt(2 / .10258658877510106)
    return {'minimum_effect': effect, 'observed_sd': sd, 'normal_approximation_n': max(8, math.ceil(((1.96 + .841621) * sd / effect) ** 2)),
            'upper_95pct_sd_bound': upper_sd,
            'conservative_normal_approximation_n': max(8, math.ceil(((1.96 + .841621) * upper_sd / effect) ** 2)),
            'limitations': 'Exploratory n=3 variance, approximate 80% power at alpha=.05; a confirmation needs its own frozen protocol.'}


def read_trial(path):
    raw = gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()
    return json.loads(raw)


def analyze(source):
    rows = []
    for path in sorted(source.glob('*.json*')):
        data = read_trial(path)
        if 'configuration' in data and 'result' in data:
            rows.append(summarize(data))
    block_path = source / 'block.json'
    if not block_path.exists() and (source / 'block.json.gz').exists(): block_path = source / 'block.json.gz'
    block = read_trial(block_path) if block_path.exists() else {'complete': False, 'qualified': False}
    assert len({r['trial_id'] for r in rows}) == len(rows)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['block'], row['path'], row['profile'], row['policy'], row['replicas'], row['node_cpus'], row['quota'])].append(row)
    summaries = []
    for key, trials in sorted(groups.items()):
        trials.sort(key=lambda r: r['repetition'])
        summaries.append({'condition': dict(zip(['block', 'path', 'profile', 'policy', 'replicas', 'node_cpus', 'quota'], key)),
                          'useful_cohort_per_second': interval(r['useful_cohort_per_second'] for r in trials),
                          'useful_fraction': interval(r['useful_fraction'] for r in trials)})
    return {'block': block, 'included_for_inference': block['complete'] and block['qualified'],
            'trials': rows, 'conditions': summaries,
            'rule': 'All raw trials remain described; no passing subset from an invalid block is used for causal interpretation.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'trials': len(result['trials']), 'included_for_inference': result['included_for_inference']}))
