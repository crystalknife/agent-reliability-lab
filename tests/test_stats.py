"""Tests for minilab.stats.

Wilson-interval reference values are checked against algebraic identities
of the Wilson score formula (provable from the formula itself, so a buggy
implementation is very likely to violate at least one) plus one published
textbook example, rather than against hand-transcribed decimals that could
themselves contain a transcription error.
"""

import math
import unittest

from minilab.stats import (
    SMALL_TASK_COUNT_WARNING_THRESHOLD,
    task_level_summary,
    wilson_interval,
)


class TestWilsonIntervalValidation(unittest.TestCase):
    def test_rejects_non_int_k(self):
        with self.assertRaises(ValueError):
            wilson_interval(1.5, 10)

    def test_rejects_non_int_n(self):
        with self.assertRaises(ValueError):
            wilson_interval(1, 10.0)

    def test_rejects_bool_k(self):
        # bool is a subclass of int in Python; must not silently pass as k.
        with self.assertRaises(ValueError):
            wilson_interval(True, 10)

    def test_rejects_n_zero(self):
        with self.assertRaises(ValueError):
            wilson_interval(0, 0)

    def test_rejects_negative_k(self):
        with self.assertRaises(ValueError):
            wilson_interval(-1, 10)

    def test_rejects_k_greater_than_n(self):
        with self.assertRaises(ValueError):
            wilson_interval(11, 10)

    def test_rejects_confidence_out_of_range(self):
        with self.assertRaises(ValueError):
            wilson_interval(5, 10, confidence=1.0)
        with self.assertRaises(ValueError):
            wilson_interval(5, 10, confidence=0.0)

    def test_rejects_untabulated_confidence(self):
        with self.assertRaises(ValueError):
            wilson_interval(5, 10, confidence=0.80)

    def test_near_miss_confidence_is_refused_not_rounded(self):
        # PR #14 review F2: these used to be rounded to two decimals, so 0.9549 and 0.9949
        # were silently computed with the 95% / 99% z while being labelled 0.9549 / 0.9949.
        for c in (0.9549, 0.9949, 0.951, 0.949, 0.965, 0.945, 0.9551, 0.905, 0.95 + 1e-6):
            with self.subTest(confidence=c), self.assertRaises(ValueError):
                wilson_interval(1, 10, confidence=c)

    def test_exact_supported_levels_are_unchanged(self):
        expected_z = {0.90: 1.6448536269514722, 0.95: 1.9599639845400545, 0.99: 2.5758293035489004}
        for c, z in expected_z.items():
            with self.subTest(confidence=c):
                iv = wilson_interval(3, 10, confidence=c)
                p, z2, n = 0.3, z * z, 10
                denom = 1 + z2 / n
                center = (p + z2 / (2 * n)) / denom
                half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
                self.assertEqual((iv.lower, iv.upper), (center - half, center + half))
                self.assertEqual(iv.confidence, c)

    def test_float_representation_noise_is_the_same_level(self):
        noisy = 0.9 + 0.05                       # 0.9500000000000001: the same level, not another
        self.assertNotEqual(noisy, 0.95)
        a, b = wilson_interval(3, 10, confidence=noisy), wilson_interval(3, 10, confidence=0.95)
        self.assertEqual((a.lower, a.upper), (b.lower, b.upper))


class TestWilsonIntervalAlgebraicIdentities(unittest.TestCase):
    """Properties provable directly from the Wilson score formula. Any one
    of these failing indicates an implementation bug, not a borderline
    numerical issue."""

    def test_k_zero_lower_bound_is_exactly_zero(self):
        # At p=0, center == half algebraically, so lower == 0 exactly.
        for n in (1, 2, 5, 10, 50, 1000):
            iv = wilson_interval(0, n)
            self.assertEqual(iv.lower, 0.0, f"n={n}")
            self.assertGreater(iv.upper, 0.0, f"n={n}")

    def test_k_equals_n_upper_bound_is_exactly_one(self):
        # Mirror image of the k=0 case by the p -> 1-p symmetry below.
        for n in (1, 2, 5, 10, 50, 1000):
            iv = wilson_interval(n, n)
            self.assertEqual(iv.upper, 1.0, f"n={n}")
            self.assertLess(iv.lower, 1.0, f"n={n}")

    def test_p_one_half_center_is_exactly_one_half(self):
        # When p=0.5, center = 0.5 * denom / denom = 0.5 regardless of n.
        for n in (2, 4, 10, 20, 100):
            iv = wilson_interval(n // 2, n)
            self.assertAlmostEqual(iv.point_estimate, 0.5, places=12)
            midpoint = (iv.lower + iv.upper) / 2
            self.assertAlmostEqual(midpoint, 0.5, places=9, msg=f"n={n}")

    def test_symmetry_under_k_to_n_minus_k(self):
        # Replacing successes with failures flips p -> 1-p; the Wilson
        # formula is symmetric under that flip, so lower(n-k, n) must equal
        # 1 - upper(k, n) and vice versa.
        for n, k in ((10, 3), (40, 10), (7, 1), (100, 37)):
            a = wilson_interval(k, n)
            b = wilson_interval(n - k, n)
            self.assertAlmostEqual(b.lower, 1 - a.upper, places=9, msg=f"n={n}, k={k}")
            self.assertAlmostEqual(b.upper, 1 - a.lower, places=9, msg=f"n={n}, k={k}")

    def test_interval_narrows_as_n_grows_at_fixed_p(self):
        narrow = wilson_interval(500, 1000)
        wide = wilson_interval(5, 10)
        self.assertLess(narrow.upper - narrow.lower, wide.upper - wide.lower)

    def test_interval_contains_point_estimate(self):
        for n, k in ((10, 0), (10, 10), (10, 5), (1, 0), (1, 1), (37, 12)):
            iv = wilson_interval(k, n)
            self.assertLessEqual(iv.lower, iv.point_estimate + 1e-12)
            self.assertLessEqual(iv.point_estimate, iv.upper + 1e-12)

    def test_converges_to_normal_approximation_at_large_n(self):
        # At large n the Wilson interval and the simple normal (Wald)
        # approximation should nearly coincide.
        n, k = 100_000, 50_000
        iv = wilson_interval(k, n)
        p = k / n
        z = 1.9599639845400545
        wald_half = z * math.sqrt(p * (1 - p) / n)
        self.assertAlmostEqual(iv.lower, p - wald_half, places=3)
        self.assertAlmostEqual(iv.upper, p + wald_half, places=3)


class TestWilsonIntervalPublishedReference(unittest.TestCase):
    def test_n10_k5_matches_commonly_cited_textbook_example(self):
        # n=10, x=5 (p=0.5), 95% Wilson interval is widely cited as
        # approximately (0.237, 0.763) (e.g. Wilson score interval
        # references derived from Wilson 1927 / Newcombe 1998 worked
        # examples at p=0.5).
        iv = wilson_interval(5, 10, confidence=0.95)
        self.assertAlmostEqual(iv.lower, 0.237, places=2)
        self.assertAlmostEqual(iv.upper, 0.763, places=2)

    def test_n100_k50_matches_commonly_cited_textbook_example(self):
        # n=100, x=50 (p=0.5), 95% Wilson interval is widely cited as
        # approximately (0.404, 0.596).
        iv = wilson_interval(50, 100, confidence=0.95)
        self.assertAlmostEqual(iv.lower, 0.404, places=2)
        self.assertAlmostEqual(iv.upper, 0.596, places=2)


def _rows(task_id, passes, fails):
    return (
        [{"task_id": task_id, "passed": True} for _ in range(passes)]
        + [{"task_id": task_id, "passed": False} for _ in range(fails)]
    )


class TestTaskLevelSummary(unittest.TestCase):
    def test_rejects_empty_rows(self):
        with self.assertRaises(ValueError):
            task_level_summary([])

    def test_per_task_breakdown_and_counts(self):
        rows = _rows("t1", 2, 0) + _rows("t2", 0, 2)
        summary = task_level_summary(rows)
        self.assertEqual(summary.n_tasks, 2)
        self.assertEqual(summary.total_trials, 4)
        by_id = {t.task_id: t for t in summary.per_task}
        self.assertEqual(by_id["t1"].passes, 2)
        self.assertEqual(by_id["t1"].trials, 2)
        self.assertEqual(by_id["t2"].passes, 0)
        self.assertEqual(by_id["t2"].trials, 2)

    def test_pooled_wilson_matches_direct_call(self):
        rows = _rows("t1", 3, 1) + _rows("t2", 2, 2)
        summary = task_level_summary(rows)
        direct = wilson_interval(5, 8)  # 5 passes / 8 total trials
        self.assertEqual(summary.pooled_trial_wilson, direct)

    def test_task_mean_differs_from_pooled_when_trial_counts_are_unbalanced(self):
        # t1: 9/10 passes (lots of trials, mostly passing).
        # t2: 0/1 passes (one trial, failing).
        # Pooled (trial-weighted): 9/11 ~= 81.8%.
        # Task-mean (task-weighted): (0.9 + 0.0) / 2 = 45%.
        # These must differ -- that difference is exactly why both are
        # reported rather than collapsed into one number.
        rows = _rows("t1", 9, 1) + _rows("t2", 0, 1)
        summary = task_level_summary(rows)
        self.assertAlmostEqual(summary.pooled_trial_wilson.point_estimate, 9 / 11, places=9)
        self.assertAlmostEqual(summary.task_mean_pass_rate, 0.45, places=9)
        self.assertNotAlmostEqual(
            summary.pooled_trial_wilson.point_estimate, summary.task_mean_pass_rate, places=2
        )

    def test_small_cluster_warning_present_below_threshold(self):
        rows = _rows("only-task", 5, 5)
        summary = task_level_summary(rows)
        self.assertEqual(summary.n_tasks, 1)
        self.assertIsNotNone(summary.small_cluster_warning)
        self.assertIn("1 distinct task", summary.small_cluster_warning)
        self.assertIn("not a general statistical rule", summary.small_cluster_warning)

    def test_small_cluster_warning_absent_at_or_above_threshold(self):
        rows = []
        for i in range(SMALL_TASK_COUNT_WARNING_THRESHOLD):
            rows += _rows(f"t{i}", 1, 0)
        summary = task_level_summary(rows)
        self.assertEqual(summary.n_tasks, SMALL_TASK_COUNT_WARNING_THRESHOLD)
        self.assertIsNone(summary.small_cluster_warning)

    def test_str_output_contains_both_views_and_warning(self):
        rows = _rows("t1", 1, 1)
        summary = task_level_summary(rows)
        text = str(summary)
        self.assertIn("t1", text)
        self.assertIn("pooled trial-level Wilson CI", text)
        self.assertIn("mean of per-task pass rates", text)
        self.assertIn("LIMITATION", text)


if __name__ == "__main__":
    unittest.main()
