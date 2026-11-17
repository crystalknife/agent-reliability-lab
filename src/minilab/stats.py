"""Statistical reporting over MiniLab results.

Two independent concerns, kept deliberately separate:

* wilson_interval() — a closed-form Wilson score interval for a SINGLE
  binomial proportion (k successes out of n Bernoulli trials). This is the
  correct interval when the n trials are genuinely independent draws. It
  assumes that; it does not check it.

* task_level_summary() — reports one outcome per TASK, not per trial, so
  the task (not the trial) is the unit of analysis. This project's trials
  are NOT independent draws from a large task population: a handful of
  tasks are each run with several repeats. Pooling all trials and feeding
  them to wilson_interval() as if they were independent understates the
  true uncertainty, because repeated trials on the same task are
  correlated with each other (the same task either tends to be solved or
  not; repeats mostly resample model/sampler noise around that, not a
  fresh task). task_level_summary() reports the pooled (naive) Wilson
  interval ANYWAY, clearly labeled as naive, alongside the task-level mean,
  so a reader sees both the optimistic number and the one that treats
  clustering honestly.

LIMITATION THIS MODULE DOES NOT SOLVE, and does not pretend to: with a
small number of distinct tasks (this project's suite is in the single
digits to low tens), no technique -- a Wilson interval, a cluster
bootstrap, a mixed model -- produces a tight, trustworthy estimate of
"reliability on tasks like these in general". A cluster bootstrap over 8-10
tasks is itself built from only 8-10 effectively distinguishable resampling
units; it does not create statistical power that was never collected, and
reporting a narrower interval from one would be more misleading than
reporting none. This module therefore implements NO cluster bootstrap and
NO clustered standard error estimator. Instead it reports the per-task
breakdown plus the unweighted mean of per-task rates, and surfaces a
written limitation in its return value whenever the number of distinct
tasks is small, rather than burying that caveat in a docstring no caller
has to read.

SMALL_TASK_COUNT_WARNING_THRESHOLD below is a PROJECT-SPECIFIC CAUTION
calibrated to this benchmark's own task-suite size. It is not a general
statistical rule, and a different project with a different task suite
would pick its own threshold, if any -- treat the number 15 as "small for
THIS project's task suite", not as a textbook cutoff.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# Project-specific caution, not a universal statistical rule. See module
# docstring. This project's task suite is currently 10 tasks; 15 gives a
# small amount of headroom above the current suite size rather than being
# tuned to trigger exactly at today's count.
SMALL_TASK_COUNT_WARNING_THRESHOLD = 15

# z-values for the confidence levels this project actually uses. A lookup
# table rather than a numerical inverse-CDF so this module has no scipy
# dependency; extend the table (with a cited precise value) rather than
# approximating a new confidence level.
_Z_TABLE = {
    0.90: 1.6448536269514722,
    0.95: 1.9599639845400545,
    0.99: 2.5758293035489004,
}


def _z_for_confidence(confidence: float) -> float:
    try:
        return _Z_TABLE[round(confidence, 2)]
    except KeyError as e:
        raise ValueError(
            f"no tabulated z-value for confidence={confidence!r}; "
            f"supported confidence levels: {sorted(_Z_TABLE)}"
        ) from e


@dataclass(frozen=True)
class WilsonInterval:
    """A single Wilson score interval. Assumes n independent Bernoulli
    trials; does not verify independence -- see module docstring."""

    point_estimate: float
    lower: float
    upper: float
    n: int
    k: int
    confidence: float

    def __str__(self) -> str:
        return (
            f"{self.point_estimate:.1%} "
            f"(Wilson {self.confidence:.0%} CI: {self.lower:.1%}-{self.upper:.1%}, n={self.n})"
        )


def wilson_interval(k: int, n: int, confidence: float = 0.95) -> WilsonInterval:
    """Wilson score interval for a single binomial proportion k/n.

    Treats the n trials as independent Bernoulli draws. This is the right
    interval for n genuinely independent trials (e.g. n distinct tasks,
    one pass/fail each). It is NOT automatically the right interval for n
    trials pooled across a much smaller number of distinct tasks with
    repeats per task -- that is exactly the case task_level_summary()
    exists to report honestly; this function on its own does not know or
    care where its (k, n) came from.
    """
    if not isinstance(k, int) or isinstance(k, bool):
        raise ValueError(f"k must be an int, got {type(k).__name__}")
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"n must be an int, got {type(n).__name__}")
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if k < 0 or k > n:
        raise ValueError(f"k must be in [0, n]; got k={k}, n={n}")
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    z = _z_for_confidence(confidence)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    # k=0 and k=n are exact symbolic cases (lower==0, upper==1 respectively,
    # provable directly from the formula -- see tests). Float arithmetic can
    # leave a ~1e-16-scale residual instead of an exact zero/one; pin them
    # explicitly rather than rely on rounding, so callers can trust equality.
    if k == 0:
        lower, upper = 0.0, min(1.0, center + half)
    elif k == n:
        lower, upper = max(0.0, center - half), 1.0
    else:
        lower = max(0.0, center - half)
        upper = min(1.0, center + half)
    return WilsonInterval(point_estimate=p, lower=lower, upper=upper, n=n, k=k, confidence=confidence)


@dataclass(frozen=True)
class TaskOutcome:
    """Pass/fail tally for one task across its repeated trials."""

    task_id: str
    trials: int
    passes: int

    @property
    def pass_rate(self) -> float:
        return self.passes / self.trials if self.trials else 0.0

    def __str__(self) -> str:
        return f"{self.task_id}: {self.passes}/{self.trials} ({self.pass_rate:.1%})"


@dataclass(frozen=True)
class TaskLevelSummary:
    """Two views of the same rows: pooled trial-level (naive) and
    task-level (task is the unit). Report both; do not collapse to one
    number. See module docstring for why."""

    per_task: tuple
    n_tasks: int
    total_trials: int
    pooled_trial_wilson: WilsonInterval
    task_mean_pass_rate: float
    small_cluster_warning: Optional[str] = field(default=None)

    def __str__(self) -> str:
        lines = [f"{self.n_tasks} distinct task(s), {self.total_trials} total trials:"]
        for t in self.per_task:
            lines.append(f"  {t}")
        lines.append(
            "pooled trial-level Wilson CI (NAIVE -- treats all trials as "
            f"independent, which repeated-trials-per-task violates): {self.pooled_trial_wilson}"
        )
        lines.append(
            f"mean of per-task pass rates (task is the unit of analysis): "
            f"{self.task_mean_pass_rate:.1%}"
        )
        if self.small_cluster_warning:
            lines.append(f"LIMITATION: {self.small_cluster_warning}")
        return "\n".join(lines)


def task_level_summary(rows, confidence: float = 0.95) -> TaskLevelSummary:
    """Summarize pass/fail rows two ways: pooled trial-level (naive) and
    task-level (task is the unit of analysis).

    rows: iterable of dicts/objects with at least 'task_id' and 'passed'
    (dict-style access via row['task_id'] / row['passed']). Typically
    pre-filtered by the caller to valid trials only -- this function has
    no notion of trial_status and will happily summarize provider-error
    rows if you hand it any.

    Deliberately does NOT implement a cluster bootstrap or a clustered
    standard error estimator. See module docstring: with the number of
    distinct tasks this project's suite actually has, such an estimator
    would be built from too few effectively independent resampling units
    to be trustworthy, and would not create statistical power that was
    never collected. The two numbers below are reported instead: the naive
    pooled Wilson interval (clearly labeled as an overstatement of
    certainty whenever tasks are repeated), and the unweighted mean of
    per-task pass rates (the task, not the trial, as the unit -- at the
    cost of no interval at all, because an interval over N<15 tasks would
    itself invite exactly the false precision this module exists to avoid).
    """
    rows = list(rows)
    if not rows:
        raise ValueError("task_level_summary requires at least one row")

    by_task: dict[str, list[bool]] = {}
    for r in rows:
        tid = r["task_id"]
        by_task.setdefault(tid, []).append(bool(r["passed"]))

    per_task = tuple(
        TaskOutcome(task_id=tid, trials=len(v), passes=sum(v))
        for tid, v in sorted(by_task.items())
    )
    total_n = len(rows)
    total_k = sum(1 for r in rows if r["passed"])
    pooled = wilson_interval(total_k, total_n, confidence=confidence)
    n_tasks = len(per_task)
    task_mean = sum(t.pass_rate for t in per_task) / n_tasks

    warning = None
    if n_tasks < SMALL_TASK_COUNT_WARNING_THRESHOLD:
        warning = (
            f"only {n_tasks} distinct task(s) contributed to this summary, below this "
            f"project's {SMALL_TASK_COUNT_WARNING_THRESHOLD}-task caution threshold "
            "(a caution calibrated to this benchmark's own suite size, not a general "
            "statistical rule). The pooled trial-level Wilson interval above treats "
            "repeated trials on the same task as independent samples, which they are "
            "not -- read it as optimistic, not correct. No cluster bootstrap or "
            f"clustered standard error is computed: with only {n_tasks} distinct "
            "tasks such an estimator would itself rest on too few effectively "
            "independent units to be trustworthy, and would not manufacture "
            "statistical power that was never collected."
        )

    return TaskLevelSummary(
        per_task=per_task,
        n_tasks=n_tasks,
        total_trials=total_n,
        pooled_trial_wilson=pooled,
        task_mean_pass_rate=task_mean,
        small_cluster_warning=warning,
    )


# ---------------------------------------------------------------------------
# Two independent proportions (added 2026-11-16).
#
# ASSUMPTIONS, stated once and repeated in every result object's `caveat`:
#   * Each group is n INDEPENDENT Bernoulli trials with a common success
#     probability, and the two groups are independent of each other.
#   * That is NOT true of repeated trials on a small set of tasks, and it is
#     NOT true of two arms run on the SAME tasks (those are paired/clustered).
#     Feeding pooled trial counts from this project's experiments into these
#     functions gives an interval that is too narrow and a p-value that is
#     too small. The functions cannot detect this; the caller must not make
#     a confirmatory claim from them (EXP-005 protocol section 7.5).
#   * Nothing here returns a "significant" flag, on purpose.
# ---------------------------------------------------------------------------

INDEPENDENCE_CAVEAT = (
    "assumes independent Bernoulli trials within each group and independent "
    "groups; repeated trials per task or arms sharing tasks violate this, "
    "making the interval too narrow and the p-value too small"
)


def _check_count(name_k: str, k, name_n: str, n) -> None:
    for name, v in ((name_k, k), (name_n, n)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"{name} must be an int, got {type(v).__name__}")
    if n < 1:
        raise ValueError(
            f"{name_n} must be >= 1, got {n} (a proportion over zero observations is undefined)"
        )
    if k < 0 or k > n:
        raise ValueError(f"{name_k} must be in [0, {name_n}]; got {name_k}={k}, {name_n}={n}")


def wilson_interval_or_none(k: int, n: int, confidence: float = 0.95) -> Optional[WilsonInterval]:
    """wilson_interval(), except that zero observations (n == 0, k == 0)
    returns None instead of raising. For reporting code that must render
    "no valid trials" rather than crash. Every other invalid input still
    raises ValueError exactly as wilson_interval() does."""
    if isinstance(n, int) and not isinstance(n, bool) and n == 0 and k == 0:
        return None
    return wilson_interval(k, n, confidence=confidence)


@dataclass(frozen=True)
class ProportionDifference:
    """Difference p1 - p2 of two independent proportions with a
    Newcombe-Wilson (hybrid score, Newcombe 1998 "method 10") interval."""

    p1: float
    p2: float
    difference: float
    lower: float
    upper: float
    k1: int
    n1: int
    k2: int
    n2: int
    confidence: float
    method: str = "newcombe-wilson-method-10"
    caveat: str = INDEPENDENCE_CAVEAT

    def __str__(self) -> str:
        return (
            f"{self.difference:+.1%} (Newcombe-Wilson {self.confidence:.0%} CI: "
            f"{self.lower:+.1%} to {self.upper:+.1%}; {self.k1}/{self.n1} vs {self.k2}/{self.n2})"
        )


def newcombe_wilson_difference(k1: int, n1: int, k2: int, n2: int,
                               confidence: float = 0.95) -> ProportionDifference:
    """Interval for p1 - p2 (group 1 minus group 2), Newcombe (1998) method 10.

    With (l_i, u_i) the Wilson bounds of group i and d = p1 - p2:
        lower = d - sqrt((p1 - l1)^2 + (u2 - p2)^2)
        upper = d + sqrt((u1 - p1)^2 + (p2 - l2)^2)
    No continuity correction. The interval always lies in [-1, 1] and always
    contains d. See INDEPENDENCE_CAVEAT for what it assumes.
    """
    _check_count("k1", k1, "n1", n1)
    _check_count("k2", k2, "n2", n2)
    w1 = wilson_interval(k1, n1, confidence=confidence)
    w2 = wilson_interval(k2, n2, confidence=confidence)
    p1, p2 = w1.point_estimate, w2.point_estimate
    d = p1 - p2
    lower = d - math.hypot(p1 - w1.lower, w2.upper - p2)
    upper = d + math.hypot(w1.upper - p1, p2 - w2.lower)
    # Guard float residue only; the mathematical bounds are already inside
    # [-1, 1] and around d.
    lower = max(-1.0, min(lower, d))
    upper = min(1.0, max(upper, d))
    return ProportionDifference(p1=p1, p2=p2, difference=d, lower=lower, upper=upper,
                                k1=k1, n1=n1, k2=k2, n2=n2, confidence=confidence)


def fisher_exact_two_sided(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact p-value for the 2x2 table
    [[k1, n1-k1], [k2, n2-k2]], conditioning on both margins.

    "Two-sided" here is the usual probability-based definition: the sum of
    the probabilities of all tables (same margins) that are no more probable
    than the observed one. Computed in exact integer arithmetic (math.comb),
    so there is no floating-point tolerance question in the comparison; the
    only rounding is the final integer-ratio-to-float conversion.
    Degenerate margins (no successes at all, or no failures at all) give 1.0.
    """
    _check_count("k1", k1, "n1", n1)
    _check_count("k2", k2, "n2", n2)
    successes = k1 + k2
    total = n1 + n2
    lo = max(0, successes - n2)
    hi = min(n1, successes)
    weights = {x: math.comb(n1, x) * math.comb(n2, successes - x) for x in range(lo, hi + 1)}
    observed = weights[k1]
    numerator = sum(w for w in weights.values() if w <= observed)
    denominator = math.comb(total, successes)
    # Exact rational -> float. Python's int/int true division is correctly
    # rounded even for integers far beyond float range.
    return numerator / denominator


@dataclass(frozen=True)
class ProportionComparison:
    """Descriptive comparison of two independent proportions. Carries no
    significance verdict; see `caveat`."""

    group1: WilsonInterval
    group2: WilsonInterval
    difference: ProportionDifference
    fisher_exact_p_two_sided: float
    caveat: str = INDEPENDENCE_CAVEAT

    def __str__(self) -> str:
        return (
            f"group 1: {self.group1}; group 2: {self.group2}; difference {self.difference}; "
            f"Fisher exact two-sided p = {self.fisher_exact_p_two_sided:.4g} "
            f"[{self.caveat}]"
        )


def compare_independent_proportions(k1: int, n1: int, k2: int, n2: int,
                                    confidence: float = 0.95) -> ProportionComparison:
    """Wilson interval per group, Newcombe-Wilson interval for p1 - p2 and a
    two-sided Fisher exact p-value. Descriptive only: valid under the
    independence assumptions in INDEPENDENCE_CAVEAT, which this project's
    repeated-trial data usually violate."""
    diff = newcombe_wilson_difference(k1, n1, k2, n2, confidence=confidence)
    return ProportionComparison(
        group1=wilson_interval(k1, n1, confidence=confidence),
        group2=wilson_interval(k2, n2, confidence=confidence),
        difference=diff,
        fisher_exact_p_two_sided=fisher_exact_two_sided(k1, n1, k2, n2),
    )
