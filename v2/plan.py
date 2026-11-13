"""Freeze the original workload distributions and a randomized prospective matrix."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261113


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def trace_reference(profile, repetition, rate=None):
    index = json.loads((ROOT / 'protocol/traces-r3-index.json').read_text())
    name = f'fastpath-{rate}' if rate else 'bursty' if profile == 'bursty' else 'unlimited'
    return dict(index[f'{name}:{repetition}:13:emulator:{rate}'])


def load_trace(reference, fast=False):
    encoded = (ROOT / reference['path']).read_bytes()
    if hashlib.sha256(encoded).hexdigest() != reference['sha256']:
        raise ValueError('Frozen trace checksum mismatch')
    items = json.loads(gzip.decompress(encoded))
    if fast:
        items = [{**item, 'service_seconds': 0} for item in items]
    return items


def freeze():
    blocks = {}
    def add(block, replicas, profile, policy, rep, cpus=4, quota=None, rate=None, direct=False):
        row = {'block': block, 'replicas': replicas, 'profile': profile, 'policy': policy, 'repetition': rep,
               'node_cpus': cpus, 'warmup_seconds': 3, 'measurement_seconds': 10, 'deadline_seconds': 2,
               'provider': {'mode': 'emulator', 'tokens_per_second': quota if quota is not None else
                            1000000000 if profile in ['unlimited', 'fastpath'] else 40000,
                            'requests_per_second': 1000},
               'fastpath': profile == 'fastpath', 'direct_provider': direct,
               'trace_reference': trace_reference(profile, rep, rate)}
        row['trace_decoded_sha256'] = hashlib.sha256(canonical(load_trace(row['trace_reference'], row['fastpath']))).hexdigest()
        row['trial_id'] = block + '-' + hashlib.sha256(canonical(row)).hexdigest()[:16]
        blocks.setdefault(block, []).append(row)
    for cpus in [4, 8]:
        for workers in [1, 8]:
            for rate in [180, 360]:
                for rep in range(3): add(f'calibration-{cpus}', workers, 'fastpath', 'none', rep, cpus, rate=rate)
    for workers in [1, 2, 4, 8]:
        for profile in ['unlimited', 'quota', 'bursty']:
            for policy in ['none', 'global']:
                for rep in range(3): add('primary', workers, profile, policy, rep)
    for cpus in [4, 8]:
        for workers in [1, 8]:
            for profile in ['unlimited', 'quota']:
                for policy in ['none', 'global']:
                    for rep in range(3): add('cpu-sensitivity', workers, profile, policy, rep, cpus)
    for quota in [20000, 80000]:
        for workers in [1, 8]:
            for rep in range(3): add('quota-sensitivity', workers, 'quota', 'global', rep, quota=quota)
    for quota in [20000, 40000, 80000, 1000000000]:
        for rep in range(3): add('provider-control', 1, 'unlimited', 'none', rep, quota=quota, direct=True)
    rng = random.Random(SEED)
    for rows in blocks.values(): rng.shuffle(rows)
    return {'seed': SEED, 'original_trace_seed': 20261112, 'blocks': blocks,
            'definitions': {'useful': 'HTTP200, correct, client completion by planned arrival plus deadline',
                'cohort': 'planned arrivals in [warmup,warmup+measurement), including their bounded drain',
                'window': 'useful completions in [warmup,warmup+measurement), including warmup arrivals'},
            'gate': {'emitted_fraction': 1, 'emission_lag_p95_seconds': .05,
                     'fastpath_useful_fraction': .99, 'exclude': 'whole modified or technically invalid block'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'protocol/v2-emulator-plan.json')
    args = parser.parse_args()
    value = json.dumps(freeze(), indent=2) + '\n'
    if args.out.exists() and args.out.read_text() != value:
        raise RuntimeError('Refusing to overwrite a frozen plan')
    args.out.write_text(value)
    print(json.dumps({key: len(rows) for key, rows in freeze()['blocks'].items()}))
