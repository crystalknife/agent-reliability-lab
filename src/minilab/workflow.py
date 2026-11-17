"""Workflow-dependency graphs for tasks (opt-in; schema 2).

This module is deliberately separate from `tasks.py`. The ten legacy tasks
are schema 1 and carry NO graph; their definitions are not touched by
anything here. A graph is something a schema-2 task (or a separately kept
legacy-anchor table, see `legacy_anchors.py`) may declare.

What a graph claims
-------------------
An operation is one tool call a correct solution makes. An edge says the
operation needs something from an earlier one. Two edge kinds are kept
apart because they are different claims:

* "information": the dependent call cannot be issued without a value that
  only the earlier call's output provides (e.g. an account ID that is not in
  the prompt). Each information edge names that value in `via`.
* "procedural": the prompt or a policy asks for an order (e.g. "confirm
  identity first"), but no value flows. A procedural edge never adds to the
  primary depth.

Declaring a tool as required, or listing tools in some order, is NOT
evidence of an information dependency, and nothing here infers edges from
`required_tools` or from call order. A graph is an author's claim; this
module checks that the claim is well formed and computes its metrics. It
cannot check that the claim is TRUE (that an agent could not get the value
another way). That is the job of the bypass/guessing audit, which is not
implemented here.

Necessity
---------
* "mandatory": needed for a correct answer; counted everywhere.
* "obligation": the task demands the call, but nothing flows from it
  (e.g. an identity confirmation whose output is not used). Counted in
  operation count and width; may not have information edges.
* "optional": a correct answer exists without it. Excluded from the primary
  metrics; included only in the "with optional" view.

Metrics (all computed, never hand-entered)
------------------------------------------
* operation_count: number of selected operations.
* depth: number of operations on the longest dependency chain (1 for a
  single operation, 0 for an empty selection).
* width: the largest number of operations sharing one depth level. A
  practical proxy for parallel breadth, NOT a maximum-antichain size.
* max_fan_in ("branching"): the most dependencies converging on one
  operation. max_fan_out: the most dependents of one operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EDGE_KINDS = ("information", "procedural")
NECESSITIES = ("mandatory", "obligation", "optional")


@dataclass(frozen=True)
class Edge:
    op: str          # op_id of the operation depended on
    kind: str        # one of EDGE_KINDS
    via: str = ""    # the value that flows (information edges)


@dataclass(frozen=True)
class Operation:
    op_id: str
    tool: str
    depends_on: tuple = ()
    necessity: str = "mandatory"


@dataclass(frozen=True)
class GraphMetrics:
    operation_count: int
    depth: int
    width: int
    max_fan_in: int
    max_fan_out: int
    level_sizes: tuple = ()
    op_depths: dict = field(default_factory=dict, compare=False)

    @property
    def branching(self) -> int:
        """Alias for max_fan_in (the name the earlier draft used)."""
        return self.max_fan_in


def normalize_operations(raw_ops, *, label="graph", allow_untyped_edges=False):
    """Parse and structurally check an `operations` list.

    Each operation is a dict with `op_id`, `tool`, `depends_on` and an
    optional `necessity`. Each dependency is a dict
    {"op": <op_id>, "kind": "information"|"procedural", "via": <str>}.
    With allow_untyped_edges=True a bare op_id string is accepted and read
    as an information edge with an unstated `via` (the form the 2026-11-13
    draft used); schema-2 tasks must not use it.

    Raises ValueError. Does not check tool names or cycles (see
    validate_graph).
    """
    if not isinstance(raw_ops, list) or not raw_ops:
        raise ValueError(f"{label}: 'operations' must be a non-empty list")
    ops = []
    seen = set()
    for raw in raw_ops:
        if not isinstance(raw, dict) or not {"op_id", "tool", "depends_on"} <= set(raw):
            raise ValueError(f"{label}: each operation needs 'op_id', 'tool', and 'depends_on'")
        op_id = raw["op_id"]
        if not isinstance(op_id, str) or not op_id:
            raise ValueError(f"{label}: operation 'op_id' must be a non-empty string")
        if op_id in seen:
            raise ValueError(f"{label}: duplicate operation op_id {op_id!r}")
        seen.add(op_id)
        if not isinstance(raw["tool"], str) or not raw["tool"]:
            raise ValueError(f"{label}: operation {op_id!r} 'tool' must be a non-empty string")
        necessity = raw.get("necessity", "mandatory")
        if necessity not in NECESSITIES:
            raise ValueError(
                f"{label}: operation {op_id!r} has unknown necessity {necessity!r} "
                f"(use one of {NECESSITIES})")
        deps = raw["depends_on"]
        if not isinstance(deps, list):
            raise ValueError(f"{label}: operation {op_id!r} 'depends_on' must be a list")
        edges = []
        targets = set()
        for dep in deps:
            if isinstance(dep, str):
                if not allow_untyped_edges:
                    raise ValueError(
                        f"{label}: operation {op_id!r} has an untyped dependency {dep!r}; "
                        "write {'op': ..., 'kind': 'information'|'procedural', 'via': ...}")
                edge = Edge(op=dep, kind="information", via="")
            elif isinstance(dep, dict) and isinstance(dep.get("op"), str) and dep.get("op"):
                kind = dep.get("kind")
                if kind not in EDGE_KINDS:
                    raise ValueError(
                        f"{label}: operation {op_id!r} dependency on {dep['op']!r} has "
                        f"unknown kind {kind!r} (use one of {EDGE_KINDS})")
                via = dep.get("via", "")
                if not isinstance(via, str):
                    raise ValueError(f"{label}: operation {op_id!r} dependency 'via' must be a string")
                if kind == "information" and not via.strip():
                    raise ValueError(
                        f"{label}: operation {op_id!r} information dependency on {dep['op']!r} "
                        "must name the value that flows in 'via'")
                edge = Edge(op=dep["op"], kind=kind, via=via)
            else:
                raise ValueError(
                    f"{label}: operation {op_id!r} 'depends_on' entries must be dicts with "
                    "'op' and 'kind' (or op_id strings where untyped edges are allowed)")
            if edge.op in targets:
                raise ValueError(
                    f"{label}: operation {op_id!r} lists dependency {edge.op!r} more than once")
            targets.add(edge.op)
            edges.append(edge)
        ops.append(Operation(op_id=op_id, tool=raw["tool"], depends_on=tuple(edges),
                             necessity=necessity))
    return ops


def _find_cycle(graph):
    """Return one cycle as a list of ids (first == last), or None.
    Iterative DFS, so deep graphs cannot hit the recursion limit."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    for start in graph:
        if color[start] != WHITE:
            continue
        path = [start]
        iters = [iter(graph[start])]
        color[start] = GRAY
        while iters:
            for nxt in iters[-1]:
                if color[nxt] == GRAY:
                    return path[path.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    path.append(nxt)
                    iters.append(iter(graph[nxt]))
                    break
            else:
                color[path.pop()] = BLACK
                iters.pop()
    return None


def validate_graph(ops, *, known_tools=None, label="graph"):
    """Check a normalized graph: known tools, no dangling references, no
    cycles (over ALL edges, both kinds), and the necessity rules:
    an obligation has no information edges in or out, and a non-optional
    operation never has an information dependency on an optional one.
    Raises ValueError; returns True."""
    ids = {o.op_id for o in ops}
    by_id = {o.op_id: o for o in ops}
    if known_tools is not None:
        for o in ops:
            if o.tool not in known_tools:
                raise ValueError(
                    f"{label}: operation {o.op_id!r} has unknown tool {o.tool!r} "
                    f"(known tools: {list(known_tools)})")
    for o in ops:
        dangling = [e.op for e in o.depends_on if e.op not in ids]
        if dangling:
            raise ValueError(
                f"{label}: operation {o.op_id!r} depends on undeclared operation id(s) {dangling}")
    cycle = _find_cycle({o.op_id: [e.op for e in o.depends_on] for o in ops})
    if cycle:
        raise ValueError(f"{label}: dependency cycle: {' -> '.join(cycle)}")
    for o in ops:
        for e in o.depends_on:
            if e.kind != "information":
                continue
            dep = by_id[e.op]
            if o.necessity == "obligation" or dep.necessity == "obligation":
                raise ValueError(
                    f"{label}: obligation operations carry no information edges "
                    f"({o.op_id!r} <- {e.op!r}); if a value flows, the operation is mandatory")
            if o.necessity != "optional" and dep.necessity == "optional":
                raise ValueError(
                    f"{label}: {o.necessity} operation {o.op_id!r} has an information "
                    f"dependency on optional operation {e.op!r}; then it is not optional")
    return True


def graph_metrics(ops, *, edge_kinds=("information",),
                  necessities=("mandatory", "obligation")):
    """Metrics of the sub-graph made of operations whose necessity is in
    `necessities`, using only edges whose kind is in `edge_kinds` and whose
    both ends are selected. Assumes validate_graph passed."""
    selected = {o.op_id: o for o in ops if o.necessity in necessities}
    deps = {
        oid: [e.op for e in o.depends_on if e.kind in edge_kinds and e.op in selected]
        for oid, o in selected.items()
    }
    depth = {}
    for start in selected:           # iterative longest-path with memo
        stack = [start]
        while stack:
            oid = stack[-1]
            if oid in depth:
                stack.pop()
                continue
            pending = [d for d in deps[oid] if d not in depth]
            if pending:
                stack.extend(pending)
                continue
            depth[oid] = 1 + max((depth[d] for d in deps[oid]), default=0)
            stack.pop()
    levels = {}
    for d in depth.values():
        levels[d] = levels.get(d, 0) + 1
    fan_out = {oid: 0 for oid in selected}
    for oid, ds in deps.items():
        for d in ds:
            fan_out[d] += 1
    return GraphMetrics(
        operation_count=len(selected),
        depth=max(depth.values(), default=0),
        width=max(levels.values(), default=0),
        max_fan_in=max((len(ds) for ds in deps.values()), default=0),
        max_fan_out=max(fan_out.values(), default=0),
        level_sizes=tuple(levels[k] for k in sorted(levels)),
        op_depths=dict(depth),
    )


def tool_declaration_report(ops, required_tools):
    """Compare the tools the graph uses with a task's `required_tools`.
    A difference is information, not an error: `required_tools` is what the
    evaluator demands by name, the graph is what the author claims is
    needed, and the two are known to disagree on some legacy tasks."""
    non_optional = {o.tool for o in ops if o.necessity != "optional"}
    all_tools = {o.tool for o in ops}
    required = set(required_tools)
    return {
        "required_but_not_in_graph": sorted(required - all_tools),
        "required_but_only_optional_in_graph": sorted((required & all_tools) - non_optional),
        "in_graph_but_not_required": sorted(non_optional - required),
    }


def workflow_summary(raw_ops, *, known_tools=None, label="graph", allow_untyped_edges=False):
    """Validate and return {'primary': GraphMetrics, 'with_optional_and_procedural': GraphMetrics}.

    primary = mandatory + obligation operations, information edges only.
    This is the declared information-dependency depth. It is a property of
    the declared graph, not an audited, bypass-free depth.
    """
    ops = normalize_operations(raw_ops, label=label, allow_untyped_edges=allow_untyped_edges)
    validate_graph(ops, known_tools=known_tools, label=label)
    return {
        "primary": graph_metrics(ops),
        "with_optional_and_procedural": graph_metrics(
            ops, edge_kinds=EDGE_KINDS, necessities=NECESSITIES),
    }


# ---------------------------------------------------------------------------
# Compatibility layer for the 2026-11-13 draft, which stored untyped
# `operations` on task dicts and required the graph's tool set to equal
# `required_tools`. Kept so that draft's tests still exercise real code; new
# code should use workflow_summary / task_schema instead.
# ---------------------------------------------------------------------------

def _draft_ops(task):
    return normalize_operations(task["operations"], label=f"task {task.get('task_id', '?')}",
                                allow_untyped_edges=True)


def validate_task_with_draft_operations(task):
    """The draft's strict rule set: the base task must be valid, 'operations'
    is mandatory, tools must be known, the graph acyclic, and the graph's
    tool set must equal `required_tools` exactly."""
    from .tasks import validate_task
    from .tools import TOOL_NAMES

    validate_task(task)
    tid = task.get("task_id", "?")
    if "operations" not in task:
        raise ValueError(f"task {tid} missing keys: ['operations']")
    ops = _draft_ops(task)
    validate_graph(ops, known_tools=TOOL_NAMES, label=f"task {tid}")
    declared, required = {o.tool for o in ops}, set(task["required_tools"])
    if declared != required:
        raise ValueError(
            f"task {tid}: operations tool set {sorted(declared)} must equal "
            f"required_tools {sorted(required)} exactly")
    return True


def task_operation_count(task):
    return graph_metrics(_draft_ops(task), edge_kinds=EDGE_KINDS, necessities=NECESSITIES).operation_count


def task_depth(task):
    return graph_metrics(_draft_ops(task), edge_kinds=EDGE_KINDS, necessities=NECESSITIES).depth


def task_width(task):
    return graph_metrics(_draft_ops(task), edge_kinds=EDGE_KINDS, necessities=NECESSITIES).width


def task_branching(task):
    return graph_metrics(_draft_ops(task), edge_kinds=EDGE_KINDS, necessities=NECESSITIES).max_fan_in
