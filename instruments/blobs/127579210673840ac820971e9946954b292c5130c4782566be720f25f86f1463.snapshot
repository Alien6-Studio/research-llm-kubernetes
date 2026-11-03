"""Freeze the independently hosted model path without changing loads or prompts."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
from v2.plan import ROOT


def derive(gateway):
    address = ipaddress.ip_address(gateway)
    if address.version != 4 or not address.is_private or address.is_unspecified or address.is_loopback:
        raise ValueError('Use the dedicated VM Docker bridge gateway')
    original = ROOT / 'protocol/v2-model/plan.json'
    plan = json.loads(original.read_text())
    plan['derived_from'] = {'path': 'protocol/v2-model/plan.json', 'sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                            'change': 'Private SSH/bridge endpoint and independent client VM; rates, traces, tasks and options unchanged.'}
    for rows in plan['blocks'].values():
        for config in rows: config['provider']['ollama_url'] = f'http://{gateway}:11435'
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--out', type=Path, default=ROOT / 'protocol/v2-remote-model-plan.json')
    args = parser.parse_args()
    if args.out.exists(): raise RuntimeError('Refusing to overwrite a registered endpoint plan')
    args.out.write_text(json.dumps(derive(args.gateway), indent=2) + '\n')
