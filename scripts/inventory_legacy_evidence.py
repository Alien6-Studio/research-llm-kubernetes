"""Count publicly indexed historical evidence without running an experiment."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2.verify import verify_direct_model_answers, verify_standalone_warmup


def inventory(root=ROOT):
    files = trials = predictions = 0
    blocks = {}
    for manifest in sorted((root / 'archives').glob('v2-*/manifest.json')):
        count = 0
        for entry in json.loads(manifest.read_bytes())['files']:
            path = (root / entry['path']).resolve()
            if not path.is_relative_to(root.resolve()): raise ValueError('Archive escapes the repository')
            encoded = path.read_bytes(); raw = gzip.decompress(encoded)
            if (hashlib.sha256(encoded).hexdigest() != entry['sha256']
                    or hashlib.sha256(raw).hexdigest() != entry['decoded_sha256']
                    or len(raw) != entry['decoded_bytes']):
                raise ValueError('Indexed historical bytes differ')
            files += 1
            if not path.name.endswith('.json.gz'): continue
            document = json.loads(raw)
            if 'observations' in document and 'metadata' in document and 'valid' in document:
                predictions += verify_standalone_warmup(document)
            if 'warmup' in document and 'results' in document and 'metadata' in document:
                predictions += len(document['warmup']) + sum(len(row['observations']) for row in document['results'])
            if 'configuration' not in document or 'result' not in document: continue
            count += 1; trials += 1
            config, result = document['configuration'], document['result']
            if config['provider']['mode'] == 'ollama':
                predictions += sum('inference' in row for row in result['role_records']['provider']['records'])
                if config.get('direct_ollama'):
                    items = {item['id']: item for item in config['trace']}
                    predictions += sum(verify_direct_model_answers(row, items[row['id']]) for row in result['requests'])
        blocks[manifest.relative_to(root).as_posix()] = count
    return {'files': files, 'trials': trials, 'model_predictions': predictions, 'blocks': blocks}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--check', action='store_true')
    args = parser.parse_args(); result = inventory(); target = ROOT / 'v2/evidence-counts.json'
    if args.check:
        if json.loads(target.read_bytes()) != result: raise ValueError('Historical evidence inventory differs')
    else:
        target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
