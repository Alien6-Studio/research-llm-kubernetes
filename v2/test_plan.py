"""The preregistered matrix is complete, paired and deterministic."""
import unittest
from v2.plan import canonical, freeze, load_trace


class MatrixTests(unittest.TestCase):
    def test_original_72_conditions_and_sensitivities_are_complete(self):
        plan = freeze()
        self.assertEqual(len(plan['blocks']['primary']), 72)
        self.assertEqual(len(plan['blocks']['cpu-sensitivity']), 48)
        self.assertEqual(len(plan['blocks']['quota-sensitivity']), 12)
        for name in ['calibration-4', 'calibration-8', 'provider-control']:
            self.assertEqual(len(plan['blocks'][name]), 12)
        self.assertEqual(plan, freeze())
        ids = [r['trial_id'] for rows in plan['blocks'].values() for r in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_quota_and_unlimited_traces_are_identical_in_every_paired_arm(self):
        rows = freeze()['blocks']['primary']
        for rep in range(3):
            signatures = {r['trace_decoded_sha256'] for r in rows if r['repetition'] == rep and r['profile'] != 'bursty'}
            self.assertEqual(len(signatures), 1)
        self.assertEqual({r['provider']['tokens_per_second'] for r in rows if r['profile'] == 'quota'}, {40000})


if __name__ == '__main__':
    unittest.main()
