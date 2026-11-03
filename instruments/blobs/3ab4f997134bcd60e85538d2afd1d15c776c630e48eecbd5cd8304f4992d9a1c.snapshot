"""Direct-model reference retains late generation rather than hiding its work."""
import asyncio
import hashlib
from pathlib import Path
import time

from v2.service import Application
from v2.transport import request, serve
from v2.workload import correct, model_request


class ModelApplication(Application):
    def stats(self):
        return {**super().stats(), 'model_controller_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    async def invoke(self, item, config, start):
        if not config.get('direct_ollama'):
            return await super().invoke(item, config, start)
        scheduled = start + item['offset']
        deadline = scheduled + config['deadline_seconds']
        launched = time.monotonic()
        row = {'id': item['id'], 'clock_domain': self.domain, 'scheduled': scheduled, 'launched': launched,
               'trial_id': config['trial_id'],
               'emitted': None, 'status': 0, 'correct': False, 'scheduling_lag_seconds': max(0, launched - scheduled)}
        if launched >= deadline:
            row.update(reason='generator_not_emitted_deadline', finished=time.monotonic())
        else:
            def sent(): row['emitted'] = time.monotonic()
            pending = asyncio.create_task(request(config['provider']['ollama_url'] + '/api/generate',
                             model_request(item), timeout=120, sent=sent))
            try:
                status, raw = await asyncio.wait_for(asyncio.shield(pending), max(.001, deadline - time.monotonic()))
                row.update(status=status, response={'inference': raw, 'correct': status == 200 and correct(raw, item)},
                           correct=status == 200 and correct(raw, item), finished=time.monotonic())
            except TimeoutError:
                row.update(reason='client_deadline', finished=time.monotonic())
                try:
                    status, raw = await pending
                    row['late_response'] = {'status': status, 'inference': raw, 'correct': status == 200 and correct(raw, item)}
                except Exception as error:
                    row['late_error'] = type(error).__name__
            except Exception as error:
                row.update(reason='client_' + type(error).__name__, finished=time.monotonic())
            row['model_call_drained_at'] = time.monotonic()
        row['latency_seconds'] = row['finished'] - scheduled
        row['emitted_latency_seconds'] = None if row['emitted'] is None else row['finished'] - row['emitted']
        return row


async def main():
    app = ModelApplication()
    heartbeat = asyncio.create_task(app.heartbeat())
    server = await serve(app.handle)
    try:
        async with server: await server.serve_forever()
    finally:
        heartbeat.cancel()


if __name__ == '__main__':
    asyncio.run(main())
