"""Prompt-sized /api/show transport controls; these calls perform no inference."""
import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from v2.identity import capture, verify_worktree
from v2.plan import ROOT
from v2.run import percentile
from v2.service import clock_domain
from v2.workload import MODEL, model_request


async def observe(url, item, start):
    scheduled = start + item['offset']
    row = {'id': item['id'], 'clock_domain': clock_domain(), 'scheduled': scheduled, 'launched': time.monotonic(), 'emitted': None, 'status': 0}
    target = urlsplit(url)
    fixture = target.path == '/transport-control'
    body = json.dumps(model_request(item) if fixture else {'model': MODEL, 'benchmark_padding': item['prompt_text']}, separators=(',', ':')).encode()
    phases = {'clock': 'time.monotonic', 'clock_scope': 'requesting_process', 'request_task_started': row['launched']}
    row['transport_phases'] = phases
    writer = None
    try:
        async with asyncio.timeout(10):
            phases['connection_started'] = time.monotonic()
            reader, writer = await asyncio.open_connection(target.hostname, target.port or 80)
            phases['connection_established'] = time.monotonic()
            phases['write_started'] = time.monotonic()
            writer.write(f'POST {target.path} HTTP/1.1\r\nHost: {target.hostname}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            await writer.drain()
            row['emitted'] = time.monotonic()
            phases['emitted'] = row['emitted']
            row['request_body_bytes'] = len(body)
            row['request_body_sha256'] = hashlib.sha256(body).hexdigest()
            row['status'] = int((await reader.readline()).split()[1])
            phases['status_received'] = time.monotonic()
            headers = {}
            while line := await reader.readline():
                if line == b'\r\n': break
                key, value = line.decode().split(':', 1)
                headers[key.lower()] = value.strip()
            phases['headers_received'] = time.monotonic()
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
            phases['body_received'] = time.monotonic()
            row['response_body_bytes'] = len(raw)
            row['response_body_sha256'] = hashlib.sha256(raw).hexdigest()
            parsed = json.loads(raw)
            phases['body_decoded'] = time.monotonic()
            row['model_metadata_returned'] = 'model_info' in parsed
            row['transport_fixture_returned'] = parsed.get('transport_control') is True and len(parsed.get('padding', '')) == 8192
    except Exception as error:
        row['error'] = type(error).__name__
    finally:
        if writer:
            phases['close_started'] = time.monotonic()
            writer.close()
            try: await writer.wait_closed()
            except (OSError, ConnectionError): pass
            phases['close_complete'] = time.monotonic()
    row['finished'] = time.monotonic()
    row['latency_seconds'] = row['finished'] - scheduled
    return row


def headroom_references(plan_path):
    """Bind the transport load to all three headroom traces of the selected plan."""
    references = {}
    plan_path = plan_path.resolve()
    if not plan_path.is_relative_to(ROOT):
        raise ValueError('The frozen model plan must be retained inside the study repository')
    encoded = plan_path.read_bytes()
    plan = json.loads(encoded)
    for config in plan['blocks']['model-qualification']:
        if config['profile'] != 'headroom':
            continue
        repetition, reference = config['repetition'], config['trace_reference']
        if repetition in references and references[repetition] != reference:
            raise ValueError('Headroom trace differs between qualification replica arms')
        references[repetition] = reference
    if set(references) != {0, 1, 2}:
        raise ValueError('Transport qualification requires all three headroom repetitions')
    for reference in references.values():
        path = (ROOT / reference['path']).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError('Headroom trace differs from its frozen plan')
    return references, {'plan_path': str(plan_path.relative_to(ROOT)), 'plan_sha256': hashlib.sha256(encoded).hexdigest()}


async def collect(url, out, plan_path=None):
    snapshot = capture(ROOT)
    verify_worktree(ROOT, snapshot.source_id)
    state = {**snapshot.metadata(), 'source_commit': snapshot.git_base,
             'utc': datetime.now(timezone.utc).isoformat(), 'scope': 'No-inference transport fixture' if url.endswith('/transport-control') else 'Metadata-only SSH/API transport, no model generation',
             'endpoint': url, 'trials': [], 'qualified': False}
    references = None
    if plan_path is not None:
        references, identity = headroom_references(plan_path)
        state.update(identity)
    out.write_text(json.dumps(state, indent=2) + '\n')
    for rep in range(3):
        trace_path = ROOT / (references[rep]['path'] if references is not None else f'protocol/v2-model/headroom-{rep}.json.gz')
        items = json.loads(gzip.decompress(trace_path.read_bytes()))
        start = time.monotonic() + .25
        pending = []
        for item in items:
            await asyncio.sleep(max(0, start + item['offset'] - time.monotonic()))
            pending.append(asyncio.create_task(observe(url, item, start)))
        rows = await asyncio.gather(*pending)
        lag = percentile([r['emitted'] - r['scheduled'] for r in rows if r['emitted'] is not None], .95)
        latency = percentile([r['latency_seconds'] for r in rows], .95)
        passed = bool(rows) and all(r['status'] == 200 and (r.get('transport_fixture_returned') if url.endswith('/transport-control') else r.get('model_metadata_returned')) and r['emitted'] is not None and 'error' not in r for r in rows) and lag is not None and lag <= .05 and latency is not None and latency <= .1
        state['trials'].append({'repetition': rep, 'trace_sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                                'emission_lag_p95_seconds': lag, 'latency_p95_seconds': latency, 'passed': passed, 'requests': rows})
        if references is not None:
            state['trials'][-1]['trace_reference'] = references[rep]
        out.write_text(json.dumps(state, indent=2) + '\n')
    state['qualified'] = all(trial['passed'] for trial in state['trials'])
    out.write_text(json.dumps(state, indent=2) + '\n')
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:11435/api/show')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--plan', type=Path, help='Frozen plan supplying this campaign\'s own headroom traces')
    args = parser.parse_args()
    if args.out.exists(): raise RuntimeError('Do not overwrite a transport control')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not asyncio.run(collect(args.url, args.out, args.plan))['qualified']: raise SystemExit(1)
