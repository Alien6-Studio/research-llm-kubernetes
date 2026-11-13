"""Deadline bookkeeping must retain real work completed after client expiry."""
import asyncio
import time
import unittest
from unittest.mock import patch

from v2.model_service import ModelApplication


class ModelDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_generation_is_drained_but_is_never_counted_as_useful(self):
        async def slow(*args, sent=None, **kwargs):
            if sent: sent()
            await asyncio.sleep(.06)
            return 200, {'response': '{"label":"ok"}', 'eval_count': 6}
        item = {'id': 'late', 'offset': 0, 'task_status': 200, 'prompt_text': ''}
        config = {'direct_ollama': True, 'deadline_seconds': .015, 'provider': {'ollama_url': 'http://local'}}
        with patch('v2.model_service.request', slow):
            row = await ModelApplication('controller').invoke(item, config, time.monotonic())
        self.assertEqual(row['reason'], 'client_deadline')
        self.assertEqual(row['status'], 0)
        self.assertFalse(row['correct'])
        self.assertTrue(row['late_response']['correct'])
        self.assertEqual(row['late_response']['inference']['eval_count'], 6)
        self.assertGreater(row['model_call_drained_at'], row['finished'])
        self.assertAlmostEqual(row['latency_seconds'], row['finished'] - row['scheduled'])
