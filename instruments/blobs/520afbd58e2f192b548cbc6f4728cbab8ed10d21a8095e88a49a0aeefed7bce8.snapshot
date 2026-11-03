"""Bind only the dedicated VM's private Docker bridge; relay to its SSH loopback."""
import argparse
import asyncio
import ipaddress
import json
import resource
import time


async def main(bind, port):
    address = ipaddress.ip_address(bind)
    if not address.is_private or address.is_unspecified or address.is_loopback:
        raise ValueError('Select the actual private Docker bridge address, never a public/wildcard address')
    counters = {'connections': 0, 'active': 0, 'bytes_to_inference': 0, 'bytes_from_inference': 0}
    async def connection(reader, writer):
        upstream = None
        counters['connections'] += 1
        counters['active'] += 1
        try:
            remote, upstream = await asyncio.open_connection('127.0.0.1', port)
            async def copy(source, target, counter):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
                    counters[counter] += len(data)
                if target.can_write_eof(): target.write_eof()
            await asyncio.gather(copy(reader, upstream, 'bytes_to_inference'), copy(remote, writer, 'bytes_from_inference'))
        except (ConnectionError, OSError):
            pass
        finally:
            counters['active'] -= 1
            writer.close()
            if upstream: upstream.close()
    async def observe():
        while True:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            print(json.dumps({'at': time.monotonic(), 'utc_seconds': time.time(),
                              'cpu_seconds': usage.ru_utime + usage.ru_stime,
                              'peak_rss_bytes': usage.ru_maxrss * 1024, **counters}), flush=True)
            await asyncio.sleep(1)
    observer = asyncio.create_task(observe())
    server = await asyncio.start_server(connection, bind, port)
    try:
        async with server: await server.serve_forever()
    finally: observer.cancel()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bind', required=True)
    parser.add_argument('--port', type=int, default=11435)
    args = parser.parse_args()
    asyncio.run(main(args.bind, args.port))
