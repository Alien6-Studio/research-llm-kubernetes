"""Verify lossless archives, frozen workloads and individual measured records offline."""
import gzip
import hashlib
import json
from pathlib import Path

from v2.plan import ROOT, canonical, load_trace
from v2.analyze import summarize, analyze


def same_numbers(expected, actual):
    """Allow only rounding from clock-span subtraction on different Python builds."""
    if isinstance(expected, dict):
        assert expected.keys() == actual.keys()
        for key in expected: same_numbers(expected[key], actual[key])
    elif isinstance(expected, list):
        assert len(expected) == len(actual)
        for left, right in zip(expected, actual): same_numbers(left, right)
    elif isinstance(expected, float):
        assert abs(expected - actual) <= 1e-12 * max(1, abs(expected)), (expected, actual)
    else: assert expected == actual, (expected, actual)
from v2.workload import correct, model_request, MODEL
from v2.run import percentile


def digest(raw): return hashlib.sha256(raw).hexdigest()


def verify():
    files, trials, predictions = 0, 0, 0
    blocks = []
    for manifest_path in sorted((ROOT / 'archives').glob('v2-*/manifest.json')):
        entries = json.loads(manifest_path.read_text())['files']
        documents = []
        for entry in entries:
            path = (ROOT / entry['path']).resolve()
            assert path.is_relative_to(ROOT), 'Archive path escapes repository'
            encoded = path.read_bytes()
            assert digest(encoded) == entry['sha256'], path
            raw = gzip.decompress(encoded)
            assert digest(raw) == entry['decoded_sha256'], path
            assert len(raw) == entry['decoded_bytes'], path
            files += 1
            if path.name.endswith('.json.gz'):
                documents.append(json.loads(raw))
        for data in documents:
            if 'warmup' in data and 'results' in data and 'metadata' in data:
                observations = data['warmup'] + [row for setting in data['results'] for row in setting['observations']]
                for row in observations:
                    assert row['correct'] == (row['status'] == 200 and correct(row['raw'], row['item']))
                    predictions += 1
                for setting in data['results']:
                    assert setting['completion_rate'] == len(setting['observations']) / setting['elapsed_seconds']
            if data.get('endpoint') and 'trials' in data:
                fixture = data['endpoint'].endswith('/transport-control')
                passes = []
                for trial in data['trials']:
                    trace_path = ROOT / f"protocol/v2-model/headroom-{trial['repetition']}.json.gz"
                    assert digest(trace_path.read_bytes()) == trial['trace_sha256']
                    trace = {r['id']: r for r in json.loads(gzip.decompress(trace_path.read_bytes()))}
                    assert {r['id'] for r in trial['requests']} == set(trace)
                    for row in trial['requests']:
                        payload = model_request(trace[row['id']]) if fixture else {'model': MODEL, 'benchmark_padding': trace[row['id']]['prompt_text']}
                        body = json.dumps(payload, separators=(',', ':')).encode()
                        if row['emitted'] is not None:
                            assert digest(body) == row['request_body_sha256']
                            assert len(body) == row['request_body_bytes']
                        same_numbers(row['finished'] - row['scheduled'], row['latency_seconds'])
                    lag = percentile([r['emitted'] - r['scheduled'] for r in trial['requests'] if r['emitted'] is not None], .95)
                    latency = percentile([r['latency_seconds'] for r in trial['requests']], .95)
                    same_numbers(lag, trial['emission_lag_p95_seconds'])
                    same_numbers(latency, trial['latency_p95_seconds'])
                    passed = bool(trial['requests']) and all(r['status'] == 200 and r.get('transport_fixture_returned' if fixture else 'model_metadata_returned') and r['emitted'] is not None and 'error' not in r for r in trial['requests']) and lag is not None and lag <= .05 and latency is not None and latency <= .1
                    assert passed == trial['passed']
                    passes.append(passed)
                assert data['qualified'] == all(passes)
        block = next((d for d in documents if 'complete' in d and 'qualified' in d), None)
        metadata = next((d for d in documents if 'source_commit' in d and 'code_sha256' in d), None)
        count = 0
        for document in documents:
            if 'configuration' not in document or 'result' not in document: continue
            config, result = document['configuration'], document['result']
            reference = config['trace_reference']
            trace = load_trace(reference, config['fastpath'])
            assert trace == config['trace'], 'Recorded workload differs from frozen inputs'
            assert digest(canonical(trace)) == config['trace_decoded_sha256']
            summarize(document)  # Independently rechecks cohorts, latency arithmetic and ledger reconciliation.
            if metadata:
                for sample in result['resource_samples']:
                    for role in [sample['controller'], sample['provider'], *sample['workers']]:
                        if role.get('code_sha256') is not None:
                            assert role['code_sha256'] == metadata['code_sha256']
                        if role.get('source_commit'):
                            assert role['source_commit'] == metadata['source_commit']
                        if role.get('trial_binding'):
                            assert role['trial_id'] == config['trial_id']
                if any(sample['controller'].get('trial_binding') for sample in result['resource_samples']):
                    for archive in result['role_records'].values():
                        assert all(row['trial_id'] == config['trial_id'] for row in archive['records'])
            items = {item['id']: item for item in trace}
            if config['provider']['mode'] == 'ollama':
                for row in result['role_records']['provider']['records']:
                    if 'inference' in row:
                        assert row['correct'] == (row['status'] == 200 and correct(row['inference'], items[row['id']]))
                        predictions += 1
                if config.get('direct_ollama'):
                    for row in result['requests']:
                        for name in ['response', 'late_response']:
                            body = row.get(name, {})
                            if body.get('inference'):
                                assert body['correct'] == correct(body['inference'], items[row['id']])
                                predictions += 1
            count += 1
            trials += 1
        if block and block['complete']:
            assert count == block['trials'], (manifest_path, count, block['trials'])
        analysis_path = ROOT / 'analysis' / manifest_path.parent.name / 'results.json'
        if analysis_path.exists(): same_numbers(json.loads(analysis_path.read_text()), analyze(manifest_path.parent))
        blocks.append({'archive': str(manifest_path.relative_to(ROOT)), 'trials': count,
                       'complete': block.get('complete') if block else None,
                       'qualified': block.get('qualified') if block else None})
    report_path = ROOT / 'analysis/v2-study/results.json'
    if report_path.exists():
        from v2.report import prepare, illustrate_deadline_loss
        report = prepare()
        report['deadline_illustration'] = illustrate_deadline_loss()
        same_numbers(json.loads(report_path.read_text()), report)
    return {'verified_files': files, 'verified_trials': trials, 'verified_model_predictions': predictions,
            'blocks': blocks, 'scope': 'Identity and deterministic record replay, not a new measurement or proof of causal validity.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
