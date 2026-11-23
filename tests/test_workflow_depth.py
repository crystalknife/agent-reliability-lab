"""Tests for the workflow-dependency DAG added to minilab.tasks: validation
(unique IDs, dangling references, cycles, tool-set parity) and the derived
depth/width/branching properties on hand-built reference graphs, plus a
check that the real task suite's values are what they should be."""

import unittest

# 2026-11-16: the DAG code moved out of minilab.tasks (legacy tasks are schema 1
# and carry no graph). These tests now exercise the same rules through the
# draft-compatibility layer in minilab.workflow, and the "real suite" values
# below are those of the 2026-11-13 draft graphs kept in
# minilab.legacy_anchors.WORKING_TREE_2026_11_13 -- an UNREVIEWED candidate
# that disagrees with the draft protocol on four tasks. Pinning them here
# guards against silent edits to that table; it does not make them correct.
from minilab.legacy_anchors import working_tree_candidate_task as get_task
from minilab.legacy_anchors import working_tree_candidate_tasks
from minilab.workflow import (
    task_branching,
    task_depth,
    task_operation_count,
    task_width,
)
from minilab.workflow import validate_task_with_draft_operations as validate_task

TASKS = working_tree_candidate_tasks()


def _task(operations, required_tools=None, **overrides):
    """Minimal otherwise-valid task dict, just enough to exercise
    _validate_operations / the depth/width/branching functions."""
    tools_used = sorted({op["tool"] for op in operations}) if required_tools is None else required_tools
    base = {
        "task_id": "synthetic",
        "difficulty": "easy",
        "customer_id": "C101",
        "prompt": "synthetic test task",
        "expected_eligible": True,
        "expected_rule": "none",
        "required_evidence": ["x"],
        "max_steps": 10,
        "required_tools": tools_used,
        "grounded_numbers": [],
        "operations": operations,
    }
    base.update(overrides)
    return base


class TestOperationValidation(unittest.TestCase):
    def test_valid_single_node_graph_passes(self):
        t = _task([{"op_id": "a", "tool": "get_account", "depends_on": []}])
        self.assertTrue(validate_task(t))

    def test_missing_operations_key_rejected(self):
        t = _task([{"op_id": "a", "tool": "get_account", "depends_on": []}])
        del t["operations"]
        with self.assertRaises(ValueError):
            validate_task(t)

    def test_empty_operations_list_rejected(self):
        t = _task([{"op_id": "a", "tool": "get_account", "depends_on": []}])
        t["operations"] = []
        with self.assertRaises(ValueError):
            validate_task(t)

    def test_duplicate_op_id_rejected(self):
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": []},
            {"op_id": "a", "tool": "search_policy", "depends_on": []},
        ], required_tools=["get_account", "search_policy"])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_dangling_dependency_rejected(self):
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": ["does-not-exist"]},
        ])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("undeclared", str(ctx.exception).lower())

    def test_unknown_tool_rejected(self):
        t = _task([{"op_id": "a", "tool": "not_a_real_tool", "depends_on": []}],
                   required_tools=["not_a_real_tool"])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("unknown tool", str(ctx.exception).lower())

    def test_direct_cycle_rejected(self):
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": ["b"]},
            {"op_id": "b", "tool": "get_transactions", "depends_on": ["a"]},
        ], required_tools=["get_account", "get_transactions"])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_longer_cycle_rejected(self):
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": ["c"]},
            {"op_id": "b", "tool": "get_transactions", "depends_on": ["a"]},
            {"op_id": "c", "tool": "calculate", "depends_on": ["b"]},
        ], required_tools=["get_account", "get_transactions", "calculate"])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_self_dependency_rejected_as_cycle(self):
        t = _task([{"op_id": "a", "tool": "get_account", "depends_on": ["a"]}])
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_operations_tool_set_must_equal_required_tools_exactly(self):
        # operations uses only get_account; required_tools additionally
        # claims calculate -- must be rejected, not silently accepted as a
        # superset/subset.
        t = _task(
            [{"op_id": "a", "tool": "get_account", "depends_on": []}],
            required_tools=["get_account", "calculate"],
        )
        with self.assertRaises(ValueError) as ctx:
            validate_task(t)
        self.assertIn("required_tools", str(ctx.exception))

    def test_disconnected_independent_components_are_valid(self):
        # Two totally unrelated roots -- no connection required between
        # every pair of operations, only within genuine dependencies.
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": []},
            {"op_id": "b", "tool": "search_policy", "depends_on": []},
        ], required_tools=["get_account", "search_policy"])
        self.assertTrue(validate_task(t))


class TestDepthWidthBranchingReferenceGraphs(unittest.TestCase):
    def test_single_node(self):
        t = _task([{"op_id": "a", "tool": "get_account", "depends_on": []}])
        self.assertEqual(task_depth(t), 1)
        self.assertEqual(task_width(t), 1)
        self.assertEqual(task_branching(t), 0)
        self.assertEqual(task_operation_count(t), 1)

    def test_linear_chain_of_four(self):
        # a -> b -> c -> d (each depends on the previous)
        t = _task([
            {"op_id": "a", "tool": "get_customer", "depends_on": []},
            {"op_id": "b", "tool": "get_account", "depends_on": ["a"]},
            {"op_id": "c", "tool": "get_transactions", "depends_on": ["b"]},
            {"op_id": "d", "tool": "calculate", "depends_on": ["c"]},
        ], required_tools=["get_customer", "get_account", "get_transactions", "calculate"])
        self.assertEqual(task_depth(t), 4)
        self.assertEqual(task_width(t), 1)
        self.assertEqual(task_branching(t), 1)
        self.assertEqual(task_operation_count(t), 4)

    def test_three_independent_roots_no_merge(self):
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": []},
            {"op_id": "b", "tool": "get_account", "depends_on": []},
            {"op_id": "c", "tool": "get_account", "depends_on": []},
        ], required_tools=["get_account"])
        self.assertEqual(task_depth(t), 1)
        self.assertEqual(task_width(t), 3)
        self.assertEqual(task_branching(t), 0)
        self.assertEqual(task_operation_count(t), 3)

    def test_diamond_merge(self):
        # a -> c, b -> c (two independent roots converge on one sink)
        t = _task([
            {"op_id": "a", "tool": "get_transactions", "depends_on": []},
            {"op_id": "b", "tool": "get_transactions", "depends_on": []},
            {"op_id": "c", "tool": "calculate", "depends_on": ["a", "b"]},
        ], required_tools=["get_transactions", "calculate"])
        self.assertEqual(task_depth(t), 2)
        self.assertEqual(task_width(t), 2)   # a and b share depth level 1
        self.assertEqual(task_branching(t), 2)  # c has fan-in 2
        self.assertEqual(task_operation_count(t), 3)

    def test_two_chains_of_different_length_take_the_longer_for_depth(self):
        # a -> b (depth 2) and c (depth 1, independent) -- depth is the
        # LONGEST chain, not the average or the total operation count.
        t = _task([
            {"op_id": "a", "tool": "get_account", "depends_on": []},
            {"op_id": "b", "tool": "get_transactions", "depends_on": ["a"]},
            {"op_id": "c", "tool": "search_policy", "depends_on": []},
        ], required_tools=["get_account", "get_transactions", "search_policy"])
        self.assertEqual(task_depth(t), 2)
        self.assertEqual(task_operation_count(t), 3)
        # operation count (3) must not be mistaken for depth (2).
        self.assertNotEqual(task_depth(t), task_operation_count(t))


class TestRealTaskSuiteValues(unittest.TestCase):
    """Pin the computed values for the actual, currently-authored task
    suite, so a future edit to a task's `operations` graph has to
    consciously update this test rather than silently drift."""

    def test_depth_values(self):
        expected = {
            "waiver-c101": 2,
            "waiver-c102": 2,
            "waiver-c103": 2,
            "retrieval-bal-a103": 1,
            "calc-total-c101": 2,
            "policy-attr-c102": 2,
            "temporal-deposit-a103": 2,
            "horizon-checking-combined": 3,
            "deposit-total-combined": 2,
            "retrieval-multi-checking": 1,
        }
        for task_id, depth in expected.items():
            self.assertEqual(task_depth(get_task(task_id)), depth, task_id)

    def test_horizon_checking_combined_is_the_deepest_and_most_branching(self):
        # The only task whose prompt explicitly requires identity
        # confirmation before account access for two separate customers,
        # converging on one combining step -- deepest and most-branching
        # by construction of the prompt's own requirement, not by
        # authoring choice.
        depths = {t["task_id"]: task_depth(t) for t in TASKS}
        branchings = {t["task_id"]: task_branching(t) for t in TASKS}
        self.assertEqual(max(depths, key=depths.get), "horizon-checking-combined")
        self.assertEqual(max(branchings, key=branchings.get), "horizon-checking-combined")

    def test_depth_is_not_derived_from_max_steps_or_required_tools_length(self):
        # Direct regression check against the explicit instruction not to
        # derive depth from either quantity: find at least one task where
        # depth, max_steps, and len(required_tools) are all different from
        # one another, proving depth is an independent computed value.
        t = get_task("waiver-c101")
        self.assertEqual(task_depth(t), 2)
        self.assertEqual(t["max_steps"], 8)
        self.assertEqual(len(t["required_tools"]), 3)
        self.assertNotEqual(task_depth(t), t["max_steps"])
        self.assertNotEqual(task_depth(t), len(t["required_tools"]))

    def test_all_tasks_still_validate(self):
        for t in TASKS:
            self.assertTrue(validate_task(t), t["task_id"])


if __name__ == "__main__":
    unittest.main()
