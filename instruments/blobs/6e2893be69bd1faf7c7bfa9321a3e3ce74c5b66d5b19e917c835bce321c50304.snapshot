"""Verify lossless archives, frozen workloads and individual measured records offline."""
import gzip
import hashlib
import json
from pathlib import Path

from v2.plan import ROOT, canonical, load_trace
from v2.analyze import summarize
from v2.workload import correct


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
        blocks.append({'archive': str(manifest_path.relative_to(ROOT)), 'trials': count,
                       'complete': block.get('complete') if block else None,
                       'qualified': block.get('qualified') if block else None})
    return {'verified_files': files, 'verified_trials': trials, 'verified_model_predictions': predictions,
            'blocks': blocks, 'scope': 'Identity and deterministic record replay, not a new measurement or proof of causal validity.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
