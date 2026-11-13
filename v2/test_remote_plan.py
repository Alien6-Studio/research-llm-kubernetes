"""The independent-client continuation must preserve all paired workloads."""
import json
import unittest
from v2.plan import ROOT
from v2.remote_plan import derive


class RemotePlanTests(unittest.TestCase):
    def test_only_inference_endpoint_changes_in_each_condition(self):
        original = json.loads((ROOT / 'protocol/v2-model/plan.json').read_text())
        derived = derive('172.18.0.1')
        for block, rows in original['blocks'].items():
            for before, after in zip(rows, derived['blocks'][block], strict=True):
                after['provider']['ollama_url'] = before['provider']['ollama_url']
                self.assertEqual(before, after)
        self.assertEqual(original['rates'], derived['rates'])

    def test_reject_public_or_wildcard_gateway(self):
        for address in ['0.0.0.0', '127.0.0.1', '8.8.8.8', '::1']:
            with self.assertRaises(ValueError): derive(address)
