"""Tools: thin wrappers over MiniBankEnv. Single dispatch entry point."""

TOOL_SCHEMAS = [
    {
        "name": "get_customer",
        "description": "Look up a customer by ID. Returns name and customer_id.",
        "params": {
            "customer_id": {"type": "string", "required": True, "description": "Customer ID, e.g. C102"},
        },
    },
    {
        "name": "get_account",
        "description": "List a customer's accounts with balances. Pass customer_id or account_id.",
        "params": {
            "customer_id": {"type": "string", "required": False, "description": "Customer ID"},
            "account_id": {"type": "string", "required": False, "description": "Account ID"},
        },
    },
    {
        "name": "get_transactions",
        "description": "List transactions for one account, optionally limited to the last N days.",
        "params": {
            "account_id": {"type": "string", "required": True, "description": "Account ID"},
            "last_n_days": {"type": "integer", "required": False, "description": "Only transactions with days_ago <= N"},
        },
    },
    {
        "name": "search_policy",
        "description": "Search bank policies by keyword. Returns matching policy ID, title and text.",
        "params": {
            "query": {"type": "string", "required": True, "description": "Keyword, e.g. waiver"},
        },
    },
    {
        "name": "calculate",
        "description": "Sandboxed arithmetic over an explicit list of numbers. No expressions.",
        "params": {
            "op": {"type": "string", "required": True, "description": "One of sum, avg, add, mul"},
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "required": True,
                "description": "Non-empty numeric list",
            },
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SCHEMAS]


def build_system_prompt():
    """Render the tool instructions from the canonical schemas. Single source of truth."""
    lines = [
        "You are a banking assistant. Use tools as JSON: "
        '{"tool": "<name>", "arguments": {...}} or {"final": "<answer>"}.',
        "Available tools:",
    ]
    for t in TOOL_SCHEMAS:
        req = sorted(p for p, spec in t["params"].items() if spec.get("required"))
        lines.append(f"- {t['name']}: {t['description']} Required: {req or 'none'}.")
    return "\n".join(lines)


def _calculate(op, values):
    if not isinstance(values, list) or not values or not all(isinstance(v, (int, float)) for v in values):
        return {"error": "calculate requires a non-empty numeric list 'values'"}
    if op == "sum":
        return {"result": sum(values)}
    if op == "avg":
        return {"result": sum(values) / len(values)}
    if op == "add" and len(values) >= 2:
        out = values[0]
        for v in values[1:]:
            out += v
        return {"result": out}
    if op == "mul" and len(values) >= 2:
        out = values[0]
        for v in values[1:]:
            out *= v
        return {"result": out}
    return {"error": f"unsupported calculate op: {op} (use sum/avg/add/mul)"}


def dispatch(env, name, args):
    """Run one tool. Returns a JSON-serializable observation.

    Raises ValueError for unknown tool names so the agent loop can
    record a tool_error termination.
    """
    args = args or {}
    if name == "get_customer":
        return env.get_customer(args.get("customer_id"))
    if name == "get_account":
        return env.get_account(customer_id=args.get("customer_id"), account_id=args.get("account_id"))
    if name == "get_transactions":
        if not args.get("account_id"):
            return {"error": "get_transactions requires account_id"}
        return env.get_transactions(args["account_id"], last_n_days=args.get("last_n_days"))
    if name == "search_policy":
        return env.search_policy(args.get("query", ""))
    if name == "calculate":
        return _calculate(args.get("op"), args.get("values"))
    raise ValueError(f"unknown tool: {name}")
