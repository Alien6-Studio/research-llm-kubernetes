"""Recompute complete qualification blocks; resource diagnostics do not identify a cause."""
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'analysis/2026-11-12-calibration'
COHORTS = [('local-v2', 'archives/2026-11-12-calibration-v2'),
           ('local-v3', 'archives/2026-11-12-calibration-v3'),
           ('linux-v3', 'archives/2026-11-12-calibration-linux-v3')]


def percentile95(values):
    values = sorted(values)
    return values[max(0, int(len(values) * .95) - 1)]


def role_diagnostics(result):
    groups = defaultdict(list)
    events = defaultdict(dict)
    for snapshot in result['resource_samples']:
        for role in ['controller', 'upstream']:
            if snapshot.get(role):
                groups[(role, snapshot[role]['pod'])].append(snapshot[role])
        for worker in snapshot['workers']:
            groups[('worker', worker['pod'])].append(worker)
    rows = []
    for (role, pod), samples in sorted(groups.items()):
        first, last = samples[0], samples[-1]
        elapsed = last.get('monotonic', last['time']) - first.get('monotonic', first['time'])
        cpu = (last['cpu_seconds'] - first['cpu_seconds']) / elapsed if elapsed > 0 else None
        start, end = result.get('started_monotonic', 0), result.get('finished_monotonic', float('inf'))
        for sample in samples:
            for event in (sample.get('heartbeat') or {}).get('retained_events_over_5ms', []):
                if start <= event['monotonic'] <= end:
                    events[role][event['monotonic']] = event
        a, b = first.get('cpu_stat', {}), last.get('cpu_stat', {})
        rows.append({'role': role, 'pod': pod, 'samples': len(samples), 'observed_mean_cpu_cores': cpu,
                     'throttled_period_delta': b.get('nr_throttled', 0) - a.get('nr_throttled', 0),
                     'throttled_usec_delta': b.get('throttled_usec', 0) - a.get('throttled_usec', 0)})
    return {'resource_windows': rows,
            'largest_retained_heartbeat_lag_seconds': {role: max(e['lag_seconds'] for e in values.values()) for role, values in events.items() if values},
            'note': 'Mean CPU and retained heartbeat pauses are diagnostics, not proof of hardware headroom or a causal attribution. Windows can overlap.'}


def analyze():
    cohorts = []
    for name, relative in COHORTS:
        manifest_path = ROOT / relative / 'manifest.json'
        if not manifest_path.exists():
            continue
        archive = json.loads(manifest_path.read_text())
        rows = []
        source_metadata = None
        seen = set()
        for entry in archive['files']:
            encoded = (ROOT / entry['path']).read_bytes()
            assert hashlib.sha256(encoded).hexdigest() == entry['sha256']
            raw = gzip.decompress(encoded)
            assert hashlib.sha256(raw).hexdigest() == entry['decoded_sha256']
            if 'manifest-calibrate' in entry['source_relative_path']:
                source_metadata = json.loads(raw)
            if 'emulator-fastpath-' not in entry['source_relative_path']:
                continue
            trial = json.loads(raw)
            config, result = trial['configuration'], trial['result']
            assert all(item['service_seconds'] == 0 for item in config['trace'])
            offsets = {r['id']: r['offset'] for r in config['trace']}
            requests = result['requests']
            assert set(offsets) == {r['id'] for r in requests} and len(offsets) == len(requests)
            assert len({r['pod'] for r in requests if r.get('pod')}) == config['replicas']
            monotonic = 'started_monotonic' in result
            start = result['started_monotonic'] if monotonic else result['started_at']
            for r in requests:
                launched = r['launched_monotonic'] if monotonic else r['launched_at']
                scheduled = r['scheduled_monotonic'] if monotonic else r['scheduled_at']
                assert abs((scheduled - start) - offsets[r['id']]) < 1e-6
                assert abs(max(0, launched - scheduled) - r['scheduling_lag_seconds']) < 1e-9
            p95 = percentile95(r['scheduling_lag_seconds'] for r in requests)
            assert (p95 > .05) == ('Generator scheduling-lag p95 exceeds 50ms' in trial['validation_issues'])
            measured = [r for r in requests if config['warmup_seconds'] <= offsets[r['id']] < config['warmup_seconds'] + config['measurement_seconds']]
            useful = sum(r['status'] == 200 and r.get('correct', False) and r['latency_seconds'] <= config['deadline_seconds'] for r in measured)
            key = (config['replicas'], config['offered_rate'], config['repetition'])
            assert key not in seen
            seen.add(key)
            rows.append({'file': entry['path'], 'workers': key[0], 'offered_rate': key[1], 'repetition': key[2],
                         'valid': trial['valid'], 'validation_issues': trial['validation_issues'],
                         'dispatch_lag_p95_ms': p95 * 1000, 'offered_requests': len(requests),
                         'measured_requests': len(measured), 'useful_responses': useful,
                         'useful_fraction': useful / len(measured), 'monotonic_accounting': monotonic, 'trace_sha256': hashlib.sha256(json.dumps(config['trace'], sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                         'resource_diagnostics': role_diagnostics(result)})
        expected = {(workers, rate, rep) for workers in [1, 8] for rate in [180, 360] for rep in range(3)}
        assert seen == expected and len(rows) == 12, (name, seen)
        assert source_metadata is not None
        if name.endswith('v3'):
            assert source_metadata['source_commit'] == '2bd49bd11c7fe935809c27d501949decf5526368'
            assert source_metadata['service_sha256'] == hashlib.sha256((ROOT / 'service.py').read_bytes()).hexdigest()
        cohorts.append({'id': name, 'source_commit': source_metadata['source_commit'], 'service_sha256': source_metadata['service_sha256'], 'environment': source_metadata['environment'], 'complete': True, 'passed': all(r['valid'] and r['useful_fraction'] >= .99 for r in rows),
                        'valid_trials': sum(r['valid'] for r in rows), 'trials': sorted(rows, key=lambda r: (r['workers'], r['offered_rate'], r['repetition']))})
    by_id = {c['id']: c for c in cohorts}
    if 'linux-v3' in by_id:
        assert [r['trace_sha256'] for r in by_id['local-v3']['trials']] == [r['trace_sha256'] for r in by_id['linux-v3']['trials']]
    return {'scope': 'Separately identified complete calibration blocks; no pooled causal effect, throughput-capacity or real-LLM inference claim.',
            'threshold_ms': 50, 'cohorts': cohorts}


def main():
    result = analyze()
    if '--check' in sys.argv:
        assert json.loads((OUT / 'results.json').read_text()) == result
        print('Reproduced every retained calibration request and all complete-block summaries.')
        return
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    lines = ['# Calibration results', '', result['scope'], '',
             'P95 dispatch lag uses the complete 13-second trace. Useful fractions use the ten-second measurement window. Three repetitions remain separate; no individual failed trial is regenerated into a passing block.', '',
             '| Cohort | Workers | Planned requests/s | P95 dispatch lag, repetition 0/1/2 (ms) | Valid trials |',
             '| --- | ---: | ---: | --- | ---: |']
    for cohort in result['cohorts']:
        for workers in [1, 8]:
            for rate in [180, 360]:
                rows = [r for r in cohort['trials'] if (r['workers'], r['offered_rate']) == (workers, rate)]
                values = ' / '.join(f"{r['dispatch_lag_p95_ms']:.1f}" for r in rows)
                lines.append(f"| {cohort['id']} | {workers} | {rate} | {values} | {sum(r['valid'] for r in rows)}/3 |")
    lines += ['', '## Interpretation', '',
              'A high response-success rate does not establish that planned load was dispatched on schedule. The original clock and later monotonic-clock cohorts are separate observations. A correction does not retrospectively identify the cause of an earlier failure.', '',
              'Resource windows report per-role mean CPU and cgroup throttling. Heartbeat events are exploratory pause diagnostics with overlapping windows and bounded retention; they do not identify a VM, scheduler, CPU or clock cause on their own. No independent human or operational causal adjudication is implied.', '',
              'Any unqualified complete block blocks confirmatory interpretation under this protocol. Lowering an arrival rate or changing the harness requires a new prospective specification and complete block. Independent Linux results remain conditional on that hosted VM and its recorded resources. A passing block does not establish bare-metal, real-model or production capacity.']
    (ROOT / 'CALIBRATION_RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps([{'cohort': c['id'], 'valid_trials': c['valid_trials'], 'passed': c['passed']} for c in result['cohorts']]))


if __name__ == '__main__':
    main()
