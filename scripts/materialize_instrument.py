"""Inspect an exact measured instrument without depending on Git history."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_id')
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    source = json.loads((ROOT/'instruments/manifest.json').read_text())['sources'][args.source_id]
    if args.destination.exists() and any(args.destination.iterdir()):
        raise ValueError('Use an empty destination; never replace a working instrument')
    for name, entry in source['files'].items():
        destination = (args.destination/name).resolve()
        if not destination.is_relative_to(args.destination.resolve()): raise ValueError('Unsafe source path')
        raw = (ROOT/entry['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']: raise ValueError('Source identity mismatch')
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
    print('Materialized captured source; use current data/protocol assets and isolated private networking for collection.')


if __name__ == '__main__': main()
