"""Tests for the two-proportion additions to minilab.stats.

Reference values come from sources that do NOT share code with the
implementation:
  * Wilson bounds: found here by bisection on the score equation
    (p_hat - p)^2 = z^2 p (1 - p) / n, not from the closed form.
  * Newcombe-Wilson difference: (a) the worked examples in Newcombe (1998),
    Statistics in Medicine 17:873-890, Table II, method 10; (b) the cases
    whose bounds follow by hand from the k=0 / k=n Wilson limits
    z^2/(n+z^2); (c) an in-test implementation built on the bisection bounds.
  * Fisher exact: brute-force enumeration with fractions.Fraction, plus two
    textbook tables (lady tasting tea 3/4 vs 1/4, p = 34/70; and the table
    [[8,2],[1,5]], p = 0.0349650...).
None of these tests establishes that the independence assumption holds for
any ARL dataset; they test arithmetic only.
"""

import math
import unittest
from fractions import Fraction

from minilab.stats import (
    INDEPENDENCE_CAVEAT,
    compare_independent_proportions,
    fisher_exact_two_sided,
    newcombe_wilson_difference,
    wilson_interval,
    wilson_interval_or_none,
)

Z95 = 1.959963984540054


def _score(p, k, n, z):
    return (k / n - p) ** 2 - z * z * p * (1 - p) / n


def _bisect(f, a, b):
    fa = f(a)
    for _ in range(200):
        m = (a + b) / 2
        fm = f(m)
        if (fa <= 0) == (fm <= 0):
            a, fa = m, fm
        else:
            b = m
    return (a + b) / 2


def reference_wilson(k, n, z=Z95):
    """Wilson bounds by root-finding on the score equation."""
    phat = k / n
    f = lambda p: _score(p, k, n, z)  # noqa: E731
    lower = 0.0 if k == 0 else _bisect(f, 0.0, phat)
    upper = 1.0 if k == n else _bisect(f, phat, 1.0)
    return lower, upper


def reference_newcombe(k1, n1, k2, n2):
    l1, u1 = reference_wilson(k1, n1)
    l2, u2 = reference_wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    d = p1 - p2
    return (d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def reference_fisher(k1, n1, k2, n2):
    s = k1 + k2
    probs = {}
    for x in range(max(0, s - n2), min(n1, s) + 1):
        probs[x] = Fraction(math.comb(n1, x) * math.comb(n2, s - x), math.comb(n1 + n2, s))
    return float(sum(p for p in probs.values() if p <= probs[k1]))


GRID = [(k, n) for n in (1, 2, 3, 7, 10, 16, 50, 116) for k in sorted({0, 1, n // 2, n - 1, n}) if 0 <= k <= n]


class TestWilsonAgainstScoreEquation(unittest.TestCase):
    def test_closed_form_matches_bisection_reference(self):
        for k, n in GRID:
            w = wilson_interval(k, n)
            lo, hi = reference_wilson(k, n)
            self.assertAlmostEqual(w.lower, lo, places=10, msg=(k, n))
            self.assertAlmostEqual(w.upper, hi, places=10, msg=(k, n))

    def test_zero_and_all_success_limits_by_hand(self):
        # k=0: upper = z^2/(n+z^2); k=n: lower = n/(n+z^2).
        for n in (1, 5, 10, 20, 1000):
            z2 = Z95 ** 2
            self.assertAlmostEqual(wilson_interval(0, n).upper, z2 / (n + z2), places=12)
            self.assertAlmostEqual(wilson_interval(n, n).lower, n / (n + z2), places=12)

    def test_large_n_is_stable_and_ordered(self):
        for k, n in ((0, 10**9), (1, 10**9), (10**9 // 2, 10**9), (10**9, 10**9)):
            w = wilson_interval(k, n)
            self.assertTrue(0.0 <= w.lower <= w.point_estimate <= w.upper <= 1.0, (k, n))
            self.assertTrue(math.isfinite(w.lower) and math.isfinite(w.upper))


class TestZeroObservations(unittest.TestCase):
    def test_wilson_interval_still_rejects_n_zero(self):
        with self.assertRaises(ValueError):
            wilson_interval(0, 0)

    def test_or_none_returns_none_only_for_zero_observations(self):
        self.assertIsNone(wilson_interval_or_none(0, 0))
        self.assertEqual(wilson_interval_or_none(3, 10), wilson_interval(3, 10))
        for bad in ((1, 0), (-1, 5), (6, 5), (True, 5), (1.0, 5)):
            with self.assertRaises(ValueError, msg=bad):
                wilson_interval_or_none(*bad)

    def test_comparison_functions_reject_empty_groups(self):
        for fn in (newcombe_wilson_difference, fisher_exact_two_sided, compare_independent_proportions):
            for args in ((0, 0, 1, 2), (1, 2, 0, 0)):
                with self.assertRaises(ValueError, msg=(fn.__name__, args)):
                    fn(*args)


class TestNewcombeWilson(unittest.TestCase):
    # Newcombe (1998) Table II, method 10 (95%), group 1 minus group 2.
    PUBLISHED = [
        ((56, 70, 48, 80), (0.0524, 0.3339)),
        ((9, 10, 3, 10), (0.1705, 0.8090)),
        ((6, 7, 2, 7), (0.0582, 0.8062)),
        ((5, 56, 0, 29), (-0.0381, 0.1926)),
        ((0, 10, 0, 20), (-0.1611, 0.2775)),
        ((0, 10, 0, 10), (-0.2775, 0.2775)),
        ((10, 10, 0, 20), (0.6791, 1.0000)),
        ((10, 10, 0, 10), (0.6075, 1.0000)),
    ]

    def test_published_examples(self):
        for args, (lo, hi) in self.PUBLISHED:
            r = newcombe_wilson_difference(*args)
            self.assertAlmostEqual(r.lower, lo, places=4, msg=args)
            self.assertAlmostEqual(r.upper, hi, places=4, msg=args)

    def test_boundary_cases_by_hand(self):
        z2 = Z95 ** 2
        u10, u20 = z2 / (10 + z2), z2 / (20 + z2)
        r = newcombe_wilson_difference(0, 10, 0, 20)
        self.assertAlmostEqual(r.lower, -u20, places=12)
        self.assertAlmostEqual(r.upper, u10, places=12)
        r = newcombe_wilson_difference(10, 10, 0, 20)
        self.assertAlmostEqual(r.lower, 1 - math.sqrt(u10 ** 2 + u20 ** 2), places=12)
        self.assertEqual(r.upper, 1.0)
        r = newcombe_wilson_difference(0, 10, 10, 10)
        self.assertEqual(r.lower, -1.0)

    def test_matches_independent_reference_over_grid(self):
        for k1, n1 in GRID:
            for k2, n2 in GRID:
                r = newcombe_wilson_difference(k1, n1, k2, n2)
                lo, hi = reference_newcombe(k1, n1, k2, n2)
                self.assertAlmostEqual(r.lower, max(-1.0, lo), places=9, msg=(k1, n1, k2, n2))
                self.assertAlmostEqual(r.upper, min(1.0, hi), places=9, msg=(k1, n1, k2, n2))

    def test_structural_properties(self):
        for k1, n1 in GRID:
            for k2, n2 in GRID:
                r = newcombe_wilson_difference(k1, n1, k2, n2)
                self.assertTrue(-1.0 <= r.lower <= r.difference <= r.upper <= 1.0, (k1, n1, k2, n2))
                s = newcombe_wilson_difference(k2, n2, k1, n1)  # swap groups
                self.assertAlmostEqual(s.lower, -r.upper, places=12)
                self.assertAlmostEqual(s.upper, -r.lower, places=12)
                # complement both groups: difference of failure rates
                c = newcombe_wilson_difference(n1 - k1, n1, n2 - k2, n2)
                self.assertAlmostEqual(c.lower, -r.upper, places=12)
                self.assertAlmostEqual(c.upper, -r.lower, places=12)

    def test_identical_groups_interval_straddles_zero(self):
        for k, n in GRID:
            r = newcombe_wilson_difference(k, n, k, n)
            self.assertEqual(r.difference, 0.0)
            self.assertLessEqual(r.lower, 0.0)
            self.assertGreaterEqual(r.upper, 0.0)

    def test_deterministic(self):
        a = newcombe_wilson_difference(12, 50, 20, 50)
        b = newcombe_wilson_difference(12, 50, 20, 50)
        self.assertEqual(a, b)

    def test_invalid_inputs(self):
        bad = [(-1, 5, 1, 5), (6, 5, 1, 5), (1, 5, 6, 5), (1.0, 5, 1, 5), (True, 5, 1, 5),
               (1, 5, 1, "5"), (1, 5, 1, 5.0), (None, 5, 1, 5)]
        for args in bad:
            with self.assertRaises(ValueError, msg=args):
                newcombe_wilson_difference(*args)
        with self.assertRaises(ValueError):
            newcombe_wilson_difference(1, 5, 1, 5, confidence=0.5)  # untabulated


class TestFisherExact(unittest.TestCase):
    def test_textbook_tables(self):
        self.assertAlmostEqual(fisher_exact_two_sided(3, 4, 1, 4), 34 / 70, places=12)
        self.assertAlmostEqual(fisher_exact_two_sided(8, 10, 1, 6), 0.03496503496503497, places=12)

    def test_matches_bruteforce_reference(self):
        for k1, n1 in GRID[:24]:
            for k2, n2 in GRID[:24]:
                self.assertAlmostEqual(fisher_exact_two_sided(k1, n1, k2, n2),
                                       reference_fisher(k1, n1, k2, n2), places=12,
                                       msg=(k1, n1, k2, n2))

    def test_degenerate_margins_and_range(self):
        self.assertEqual(fisher_exact_two_sided(0, 5, 0, 9), 1.0)
        self.assertEqual(fisher_exact_two_sided(5, 5, 9, 9), 1.0)
        for k1, n1 in GRID:
            for k2, n2 in GRID:
                p = fisher_exact_two_sided(k1, n1, k2, n2)
                self.assertTrue(0.0 < p <= 1.0, (k1, n1, k2, n2, p))
                self.assertAlmostEqual(p, fisher_exact_two_sided(k2, n2, k1, n1), places=12)

    def test_large_counts_do_not_overflow(self):
        p = fisher_exact_two_sided(1200, 5000, 1300, 5000)
        self.assertTrue(0.0 < p < 1.0 and math.isfinite(p))


class TestCompare(unittest.TestCase):
    def test_bundle_is_consistent_and_carries_caveat(self):
        c = compare_independent_proportions(15, 50, 15, 50)
        self.assertEqual(c.difference, newcombe_wilson_difference(15, 50, 15, 50))
        self.assertEqual(c.group1, wilson_interval(15, 50))
        self.assertEqual(c.fisher_exact_p_two_sided, 1.0)
        self.assertEqual(c.caveat, INDEPENDENCE_CAVEAT)
        self.assertIn("independent", str(c))
        self.assertFalse(hasattr(c, "significant"))


if __name__ == "__main__":
    unittest.main()
