"""Audit the independently qualified emulator contrasts without pooling requests as replications."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.analyze_calibration import role_diagnostics

OUT = ROOT / 'analysis/2026-11-12-focused-linux'


def quantile(values, probability):
    values = sorted(values)
    return values[max(0, int(len(values) * probability) - 1)]


def analyze():
    archive = json.loads((ROOT / 'archives/2026-11-12-focused-linux-v3/manifest.json').read_text())
    rows, workloads, metadata = [], {}, None
    observed_code_hashes = set()
    for entry in archive['files']:
        encoded = (ROOT / entry['path']).read_bytes()
        assert hashlib.sha256(encoded).hexdigest() == entry['sha256']
        raw = gzip.decompress(encoded)
        assert hashlib.sha256(raw).hexdigest() == entry['decoded_sha256']
        data = json.loads(raw)
        if 'manifest-focused' in entry['source_relative_path']:
            metadata = data
            continue
        if 'emulator-' not in entry['source_relative_path']:
            continue
        config, result = data['configuration'], data['result']
        assert config['mode'] == 'emulator' and config['policy'] == 'none' and config['max_attempts'] == 1
        canonical = json.dumps([{k: v for k, v in item.items() if k != 'id'} for item in config['trace']], sort_keys=True, separators=(',', ':'))
        signature = hashlib.sha256(canonical.encode()).hexdigest()
        repetition = config['repetition']
        assert repetition not in workloads or workloads[repetition] == signature
        workloads[repetition] = signature
        offsets = {r['id']: r['offset'] for r in config['trace']}
        assert len(result['requests']) == len(offsets)
        assert set(offsets) == {r['id'] for r in result['requests']}
        measured = [r for r in result['requests'] if 3 <= offsets[r['id']] < 13]
        in_window = [r for r in result['requests'] if 3 <= r['ended_monotonic'] - result['started_monotonic'] < 13]
        def useful(r):
            return r['status'] == 200 and r.get('correct', False) and r['latency_seconds'] <= 2
        for r in result['requests']:
            assert abs(r['latency_seconds'] - (r['ended_monotonic'] - r['scheduled_monotonic'])) < 1e-9
            assert r['latency_seconds'] >= 0
        errors = Counter(r.get('reason', 'unspecified') for r in measured if r['status'] == 500)
        resources = role_diagnostics(result)
        resets, oom_events = [], []
        for window in resources['resource_windows']:
            if window['observed_mean_cpu_cores'] is not None and window['observed_mean_cpu_cores'] < 0:
                resets.append(window['pod'])
        for sample in result['resource_samples']:
            for role in [sample.get('controller', {}), sample.get('upstream', {}), *sample['workers']]:
                observed_code_hashes.add(role['code_sha256'])
                events = role.get('memory_events', '')
                if isinstance(events, str):
                    values = dict(line.split() for line in events.splitlines())
                    if int(values.get('oom_kill', 0)):
                        oom_events.append(role.get('pod'))
        rows.append({'file': entry['path'], 'profile': config['profile'], 'workers': config['replicas'],
                     'repetition': repetition, 'valid': data['valid'], 'validation_issues': data['validation_issues'],
                     'workload_without_labels_sha256': signature,
                     'measurement_arrivals': len(measured),
                     'useful_event_completions_in_10s': sum(useful(r) for r in in_window),
                     'useful_event_completions_per_second': sum(useful(r) for r in in_window) / 10,
                     'useful_measurement_arrivals_before_deadline': sum(useful(r) for r in measured),
                     'useful_arrival_cohort_per_second': sum(useful(r) for r in measured) / 10,
                     'arrival_cohort_status_counts': dict(Counter(str(r['status']) for r in measured)),
                     'arrival_cohort_failure_reasons': dict(Counter(r.get('reason') or 'unspecified' for r in measured if not useful(r))),
                     'http_500_reasons': dict(errors),
                     'all_arrival_cohort_latency_seconds': {f'p{int(p*100)}': quantile([r['latency_seconds'] for r in measured], p) for p in [.5, .95, .99]},
                     'dispatch_lag_p95_ms': quantile([r['scheduling_lag_seconds'] for r in result['requests']], .95) * 1000,
                     'unknown_attempt_counts_in_arrival_cohort': sum('attempts' not in r for r in measured),
                     'resource_diagnostics': resources, 'observed_cpu_counter_resets': resets,
                     'observed_oom_kill_pods': sorted(set(oom_events)),
                     'provider_cumulative_counters': {k: result['provider'].get(k) for k in ['calls', 'quota_refused', 'declared_tokens_accepted']}})
    assert metadata and metadata['source_commit'] == '2bd49bd11c7fe935809c27d501949decf5526368'
    assert observed_code_hashes == {metadata['service_sha256']}
    assert metadata['service_sha256'] == hashlib.sha256((ROOT / 'service.py').read_bytes()).hexdigest()
    expected = {(profile, workers, rep) for profile in ['unlimited', 'quota'] for workers in [1, 8] for rep in range(3)}
    assert len(rows) == 12 and {(r['profile'], r['workers'], r['repetition']) for r in rows} == expected
    assert all(r['valid'] for r in rows)
    assert all(not r['observed_cpu_counter_resets'] and not r['observed_oom_kill_pods'] for r in rows)
    pairs = []
    for rep in range(3):
        by_arm = {(r['profile'], r['workers']): r for r in rows if r['repetition'] == rep}
        pairs.append({'repetition': rep,
                      'unlimited_replica_gain_per_second': by_arm['unlimited', 8]['useful_event_completions_per_second'] - by_arm['unlimited', 1]['useful_event_completions_per_second'],
                      'quota_replica_gain_per_second': by_arm['quota', 8]['useful_event_completions_per_second'] - by_arm['quota', 1]['useful_event_completions_per_second']})
    return {'scope': 'Descriptive essential contrasts on one independently hosted Linux VM after a complete passing fast-path block. Registered CPU-envelope, quota-level, admission, burst, autoscaling and real-model follow-ups remain incomplete; this is not the full confirmatory matrix.',
            'source_commit': metadata['source_commit'], 'service_sha256': metadata['service_sha256'],
            'trace_archive_sha256': metadata['trace_archive_sha256'], 'environment': metadata['environment'],
            'workload_pairing': 'Within each repetition, all four arms have identical arrival offsets, service times, declared units and tasks. Only profile labels differ in request IDs.',
            'cohort_definition': 'Window completions count useful responses ending at offsets [3,13), including warm-up arrivals. Arrival-cohort results count requests scheduled at [3,13) and completed before their own 2-second deadline, including bounded drain. These measures are reported separately.',
            'trials': sorted(rows, key=lambda r: (r['profile'], r['workers'], r['repetition'])),
            'paired_replica_gains': pairs,
            'limits': ['Three seeded repetitions are the experimental units; requests within a trial are correlated.',
                       'HTTP500 TimeoutError outcomes are retained as failures, not dropped. Attempt counts missing from failure records remain unknown.',
                       'Sampled CPU and OOM counters show only observed windows; continuous pod restart counts were not archived, so their absence cannot prove no restart.',
                       'The local Mac v3 block did not qualify. Linux contrasts are not a successful cross-host replication of a capacity effect.',
                       'No real-model inference throughput, semantic quality, GPU, cloud cost or production sizing conclusion follows.']}


def main():
    result = analyze()
    if '--check' in sys.argv:
        assert json.loads((OUT / 'results.json').read_text()) == result
        print('Reproduced all 12 essential contrasts, paired workloads, failures and both time-window definitions.')
        return
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    lines = ['# Independently hosted emulator contrasts', '', result['scope'], '',
             'The complete Linux fast-path calibration passed 12/12 trials. Every essential contrast below passed the registered integrity and dispatch checks. HTTP rejections, deadline failures and 500 TimeoutErrors remain outcomes, not exclusions.', '',
             '| Profile | Workers | Useful event completions/s, repetitions 0/1/2 | Useful arrival-cohort requests/s, repetitions 0/1/2 |',
             '| --- | ---: | --- | --- |']
    for profile in ['unlimited', 'quota']:
        for workers in [1, 8]:
            rows = [r for r in result['trials'] if (r['profile'], r['workers']) == (profile, workers)]
            event = ' / '.join(f"{r['useful_event_completions_per_second']:.1f}" for r in rows)
            cohort = ' / '.join(f"{r['useful_arrival_cohort_per_second']:.1f}" for r in rows)
            lines.append(f'| {profile} | {workers} | {event} | {cohort} |')
    lines += ['', result['cohort_definition'], '',
              'The unlimited 8-worker arm exceeds the roughly 24/s quota-limited arm on the same VM. This is evidence for the imposed emulator-quota mechanism in this controlled setup, not a real-LLM capacity claim or completion of the full validity program.', '',
              '## Limits', ''] + ['- ' + text for text in result['limits']]
    (ROOT / 'FOCUSED_RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'valid_trials': len(result['trials']), 'source_commit': result['source_commit'], 'paired_replica_gains': result['paired_replica_gains']}))


if __name__ == '__main__':
    main()
