"""Prompt-sized /api/show transport controls; these calls perform no inference."""
import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

from v2.plan import ROOT
from v2.run import command, percentile
from v2.workload import MODEL


async def observe(url, item, start):
    scheduled = start + item['offset']
    row = {'id': item['id'], 'scheduled': scheduled, 'launched': time.monotonic(), 'emitted': None, 'status': 0}
    target = urlsplit(url)
    body = json.dumps({'model': MODEL, 'benchmark_padding': item['prompt_text']}, separators=(',', ':')).encode()
    writer = None
    try:
        async with asyncio.timeout(10):
            reader, writer = await asyncio.open_connection(target.hostname, target.port or 80)
            writer.write(f'POST {target.path} HTTP/1.1\r\nHost: {target.hostname}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            await writer.drain()
            row['emitted'] = time.monotonic()
            row['request_body_bytes'] = len(body)
            row['request_body_sha256'] = hashlib.sha256(body).hexdigest()
            row['status'] = int((await reader.readline()).split()[1])
            headers = {}
            while line := await reader.readline():
                if line == b'\r\n': break
                key, value = line.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            if 'chunked' in headers.get('transfer-encoding', ''):
                chunks = []
                while size := int((await reader.readline()).split(b';')[0], 16):
                    chunks.append(await reader.readexactly(size))
                    await reader.readexactly(2)
                raw = b''.join(chunks)
            elif 'content-length' in headers:
                raw = await reader.readexactly(int(headers['content-length']))
            else:
                raw = await reader.read()
            row['response_body_bytes'] = len(raw)
            row['response_body_sha256'] = hashlib.sha256(raw).hexdigest()
            parsed = json.loads(raw)
            row['model_metadata_returned'] = 'model_info' in parsed
    except Exception as error:
        row['error'] = type(error).__name__
    finally:
        if writer:
            writer.close()
            try: await writer.wait_closed()
            except (OSError, ConnectionError): pass
    row['finished'] = time.monotonic()
    row['latency_seconds'] = row['finished'] - scheduled
    return row


async def collect(url, out):
    state = {'source_commit': command(['git', 'rev-parse', 'HEAD']).strip(),
             'utc': datetime.now(timezone.utc).isoformat(), 'scope': 'Metadata-only SSH/API transport, no model generation',
             'endpoint': url, 'trials': [], 'qualified': False}
    for rep in range(3):
        trace_path = ROOT / f'protocol/v2-model/headroom-{rep}.json.gz'
        items = json.loads(gzip.decompress(trace_path.read_bytes()))
        start = time.monotonic() + .25
        pending = []
        for item in items:
            await asyncio.sleep(max(0, start + item['offset'] - time.monotonic()))
            pending.append(asyncio.create_task(observe(url, item, start)))
        rows = await asyncio.gather(*pending)
        lag = percentile([r['emitted'] - r['scheduled'] for r in rows if r['emitted'] is not None], .95)
        latency = percentile([r['latency_seconds'] for r in rows], .95)
        passed = bool(rows) and all(r['status'] == 200 and r.get('model_metadata_returned') and r['emitted'] is not None and 'error' not in r for r in rows) and lag is not None and lag <= .05 and latency is not None and latency <= .1
        state['trials'].append({'repetition': rep, 'trace_sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                                'emission_lag_p95_seconds': lag, 'latency_p95_seconds': latency, 'passed': passed, 'requests': rows})
        out.write_text(json.dumps(state, indent=2) + '\n')
    state['qualified'] = all(trial['passed'] for trial in state['trials'])
    out.write_text(json.dumps(state, indent=2) + '\n')
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:11435/api/show')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists(): raise RuntimeError('Do not overwrite a transport control')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not asyncio.run(collect(args.url, args.out))['qualified']: raise SystemExit(1)
