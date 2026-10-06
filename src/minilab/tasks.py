"""Task definitions with ground truth. No logic, just data + validation."""

TASK_SCHEMA_VERSION = "1"

REQUIRED_KEYS = (
    "task_id", "difficulty", "customer_id", "prompt",
    "expected_eligible", "expected_rule", "required_evidence",
    "max_steps", "required_tools", "grounded_numbers",
)

TASKS = [
    # Per-task step budget (max_steps)
    #
    # The MVP sets max_steps to oracle-required invocations + one slack step:
    # the scripted oracle's model-call count (tool calls + final answer) plus
    # one spare iteration. waiver-c101 is therefore 8 (6 calls + final + 1)
    # and c102/c103 are 7 (5 calls + final + 1).
    #
    # This is NOT calibrated as an independent difficulty or reliability
    # budget: the numbers track oracle length, not task difficulty. Do not
    # read a larger max_steps as "harder task". A real per-difficulty budget
    # must be justified separately before any value here is changed.
    #
    # max_steps is the single source of truth for the interaction budget. It
    # is read per task by the runner and enforced in agent.run_agent; there is
    # deliberately no global override.
    {
        "task_id": "waiver-c101",
        "difficulty": "easy",
        "customer_id": "C101",
        "prompt": (
            "Determine whether customer C101 qualifies for a fee waiver "
            "according to the bank's policy (WAIVER-01) and explain the evidence. "
            "Cite the policy ID, the applicable rule, and the numbers you used."
        ),
        "expected_eligible": True,
        "expected_rule": "RULE-A",
        # lowercased substrings that must appear in the final answer
        "required_evidence": ["waiver-01", "rule-a", "1500"],
        # per-task step budget: oracle needs 7 iterations (6 calls + final)
        "max_steps": 8,
        # waiver-domain support check (avg balance / 30d deposits vs thresholds)
        "support": "waiver",
        # tools the trajectory must contain for the answer to count as grounded
        "required_tools": ["get_account", "get_transactions", "search_policy"],
        # numbers that must appear (numerically) in data-tool observations
        "grounded_numbers": [1800, 1200, 1500],
    },
    {
        "task_id": "waiver-c102",
        "difficulty": "medium",
        "customer_id": "C102",
        "prompt": (
            "Determine whether customer C102 qualifies for a fee waiver "
            "according to the bank's policy (WAIVER-01) and explain the evidence. "
            "Cite the policy ID, the applicable rule, and the numbers you used."
        ),
        "expected_eligible": True,
        "expected_rule": "RULE-B",
        "required_evidence": ["waiver-01", "rule-b", "600", "500"],
        # oracle needs 6 iterations (5 calls + final)
        "max_steps": 7,
        # waiver-domain support check (avg balance / 30d deposits vs thresholds)
        "support": "waiver",
        "required_tools": ["get_account", "get_transactions", "search_policy"],
        # 600 = observed 30d deposits; 500 is the policy threshold (not data)
        "grounded_numbers": [600],
    },
    {
        "task_id": "waiver-c103",
        "difficulty": "easy",
        "customer_id": "C103",
        "prompt": (
            "Determine whether customer C103 qualifies for a fee waiver "
            "according to the bank's policy (WAIVER-01) and explain the evidence. "
            "Cite the policy ID and the numbers you used."
        ),
        "expected_eligible": False,
        "expected_rule": "none",
        "required_evidence": ["waiver-01", "300", "100"],
        # oracle needs 6 iterations (5 calls + final)
        "max_steps": 7,
        # waiver-domain support check (avg balance / 30d deposits vs thresholds)
        "support": "waiver",
        "required_tools": ["get_account", "get_transactions", "search_policy"],
        "grounded_numbers": [300, 100],
    },
    {
        "task_id": "retrieval-bal-a103",
        "difficulty": "easy",
        "customer_id": "C102",
        "prompt": (
            "What is the current balance of customer C102's checking account? "
            "Report the balance and the account ID."
        ),
        # expected_eligible is schema-required but inert here: support is None
        # and the number gate judges the answer.
        "expected_eligible": True,
        "expected_rule": "none",
        "required_evidence": ["a103", "800"],
        # oracle needs 2 invocations (1 call + final)
        "max_steps": 3,
        "required_tools": ["get_account"],
        "grounded_numbers": [800],
        "support": None,
        "expected_answer": {"type": "number", "value": 800},
    },
    {
        "task_id": "calc-total-c101",
        "difficulty": "easy",
        "customer_id": "C101",
        "prompt": (
            "What is the combined balance across all of customer C101's accounts? "
            "Use the calculate tool and report the total."
        ),
        "expected_eligible": True,
        "expected_rule": "none",
        "required_evidence": ["3000"],
        # oracle needs 3 invocations (2 calls + final)
        "max_steps": 4,
        "required_tools": ["get_account", "calculate"],
        "grounded_numbers": [1800, 1200, 3000],
        "support": None,
        "expected_answer": {"type": "number", "value": 3000},
    },
    {
        "task_id": "policy-attr-c102",
        "difficulty": "medium",
        "customer_id": "C102",
        "prompt": (
            "Customer C102 was granted a fee waiver under WAIVER-01. "
            "Which rule was satisfied, RULE-A or RULE-B, and why was the "
            "other rule not satisfied? Cite the figures for both rules."
        ),
        "expected_eligible": True,
        "expected_rule": "RULE-B",
        "required_evidence": ["waiver-01", "rule-b", "600", "800"],
        # oracle needs 4 invocations (3 calls + final)
        "max_steps": 5,
        "required_tools": ["get_account", "get_transactions", "search_policy"],
        "grounded_numbers": [800, 600],
        "support": "waiver",
        "expected_answer": {"type": "text", "contains": ["rule-b", "600"]},
    },
    {
        "task_id": "temporal-deposit-a103",
        "difficulty": "medium",
        "customer_id": "C102",
        "prompt": (
            "What is the total of direct deposits into account A103 during "
            "the last 30 days? Exclude anything outside the window."
        ),
        "expected_eligible": True,
        "expected_rule": "none",
        "required_evidence": ["600"],
        # oracle needs 3 invocations (2 calls + final)
        "max_steps": 4,
        "required_tools": ["get_transactions", "calculate"],
        # 100 (T003, 40 days old) is observed but correctly excluded from the total
        "grounded_numbers": [600, 100],
        "support": None,
        "expected_answer": {"type": "number", "value": 600},
    },
    {
        "task_id": "horizon-checking-combined",
        "difficulty": "hard",
        "customer_id": "C101",
        "prompt": (
            "Customers C101 and C102 each hold a checking account. Confirm "
            "both customers' identities, confirm recent (30-day) activity on "
            "each checking account, then report the combined checking balance. "
            "Exclude any savings accounts."
        ),
        "expected_eligible": True,
        "expected_rule": "none",
        "required_evidence": ["2600", "a101", "a103"],
        # oracle needs 8 invocations (7 calls + final)
        "max_steps": 9,
        "required_tools": ["get_customer", "get_account", "calculate"],
        # 1200 (A102 savings) is observed but must be excluded: 1800+800=2600,
        # not 3800
        "grounded_numbers": [1800, 800, 2600],
        "support": None,
        "expected_answer": {"type": "number", "value": 2600},
    },
]


def validate_task(task):
    """Fail fast on malformed task definitions. Raises ValueError."""
    if not isinstance(task, dict):
        raise ValueError(f"task must be a dict, got {type(task).__name__}")
    missing = [k for k in REQUIRED_KEYS if k not in task]
    if missing:
        raise ValueError(f"task {task.get('task_id', '?')} missing keys: {missing}")
    for k in ("task_id", "difficulty", "customer_id", "prompt", "expected_rule"):
        if not isinstance(task[k], str) or not task[k].strip():
            raise ValueError(f"task {task.get('task_id', '?')}: '{k}' must be a non-empty string")
    if type(task["expected_eligible"]) is not bool:
        raise ValueError(f"task {task['task_id']}: 'expected_eligible' must be bool")
    for k in ("required_evidence", "required_tools"):
        if not isinstance(task[k], list) or not all(isinstance(e, str) and e for e in task[k]):
            raise ValueError(f"task {task['task_id']}: '{k}' must be a non-empty list of strings")
    if not task["required_evidence"] or not task["required_tools"]:
        raise ValueError(f"task {task['task_id']}: 'required_evidence'/'required_tools' must be non-empty")
    if not isinstance(task["grounded_numbers"], list) or not all(
        isinstance(n, (int, float)) and not isinstance(n, bool) for n in task["grounded_numbers"]
    ):
        raise ValueError(f"task {task['task_id']}: 'grounded_numbers' must be a list of numbers")
    if not isinstance(task["max_steps"], int) or isinstance(task["max_steps"], bool) or task["max_steps"] < 1:
        raise ValueError(f"task {task['task_id']}: 'max_steps' must be a positive integer")
    if "support" in task and task["support"] not in ("waiver", None):
        raise ValueError(f"task {task['task_id']}: 'support' must be 'waiver' or None")
    if "expected_answer" in task:
        _validate_expected_answer(task)
    return True


def _validate_expected_answer(task):
    """Optional answer gate. Absent = current waiver path. Strict when present."""
    spec = task["expected_answer"]
    tid = task["task_id"]
    if not isinstance(spec, dict) or spec.get("type") not in ("number", "enum", "text"):
        raise ValueError(f"task {tid}: 'expected_answer.type' must be number/enum/text")
    if spec["type"] == "number":
        v = spec.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"task {tid}: number 'expected_answer' needs a numeric 'value'")
    elif spec["type"] == "enum":
        vals = spec.get("values")
        if not isinstance(vals, list) or not vals or not all(isinstance(x, str) and x for x in vals):
            raise ValueError(f"task {tid}: enum 'expected_answer' needs non-empty string 'values'")
        if spec.get("expected") not in vals:
            raise ValueError(f"task {tid}: enum 'expected' must be one of 'values'")
    else:
        items = spec.get("contains")
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x for x in items):
            raise ValueError(f"task {tid}: text 'expected_answer' needs non-empty string 'contains'")


def validate_tasks(tasks):
    seen = set()
    for t in tasks:
        validate_task(t)
        if t["task_id"] in seen:
            raise ValueError(f"duplicate task_id: {t['task_id']}")
        seen.add(t["task_id"])
    return True


validate_tasks(TASKS)


def get_task(task_id):
    for t in TASKS:
        if t["task_id"] == task_id:
            return t
    raise KeyError(f"unknown task: {task_id}")
