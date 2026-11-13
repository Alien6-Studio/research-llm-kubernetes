"""Trial-level uncertainty controls with known arithmetic, not request resampling."""
import unittest
from v2.analyze import interval, planning


class StatisticalTests(unittest.TestCase):
    def test_three_trial_interval_uses_two_degrees_of_freedom(self):
        result = interval([1, 2, 3])
        self.assertEqual(result['n'], 3)
        self.assertEqual(result['mean'], 2)
        self.assertEqual(result['sd'], 1)
        self.assertAlmostEqual(result['descriptive_95pct_t_interval'][0], -.4841377117)
        self.assertAlmostEqual(result['descriptive_95pct_t_interval'][1], 4.4841377117)

    def test_variance_uncertainty_increases_prospective_confirmation_size(self):
        result = planning(3, 1)
        self.assertGreater(result['conservative_normal_approximation_n'], result['normal_approximation_n'])
        self.assertGreaterEqual(planning(0, 1)['normal_approximation_n'], 8)
