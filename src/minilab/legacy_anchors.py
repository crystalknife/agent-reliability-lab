"""Candidate workflow graphs for the ten legacy (schema-1) tasks.

STATUS: UNREVIEWED CANDIDATES. NEITHER TABLE IS AUTHORITATIVE.

The legacy task definitions in `tasks.py` carry no graph and are not
modified. Two different sets of graphs have been proposed for them, and
they disagree on four tasks. Which one (if either) labels the legacy
anchors is an owner decision (EXP-005 decisions U12/D12; see the 3.6
task-definition audit). This module keeps both so the disagreement is
visible and testable instead of being decided silently by whichever code
happened to be written first.

* WORKING_TREE_2026_11_13: the graphs from the uncommitted `tasks.py` edit
  of 2026-11-13, copied verbatim (untyped edges). That draft required the
  graph's tool set to equal `required_tools`, so it inherits what
  `required_tools` over- or under-specifies.
* PROTOCOL_3_4_DRAFT: my encoding of the "corrected graphs" table in the
  UNFROZEN draft protocol (EXP-005-protocol.md sections 3.4 and 3.5),
  using typed edges and necessity. Where the protocol is conditional
  ("1 or 2", "if mandatory") the calculation is encoded as optional and the
  open point is listed in OPEN_QUESTIONS. This is an interpretation of a
  draft, not a reviewed derivation.

Neither table says anything about audited, bypass-free depth.
"""

from .tasks import TASKS, get_task
from .tools import TOOL_NAMES
from .workflow import workflow_summary

STATUS = "UNREVIEWED_CANDIDATE"
AUTHORITATIVE_TABLE = None   # set only by a recorded owner decision

WORKING_TREE_2026_11_13 = {'waiver-c101': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': ['accounts']},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'waiver-c102': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': ['accounts']},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'waiver-c103': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': ['accounts']},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'retrieval-bal-a103': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []}],
 'calc-total-c101': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                     {'op_id': 'total', 'tool': 'calculate', 'depends_on': ['accounts']}],
 'policy-attr-c102': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                      {'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': ['accounts']},
                      {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'temporal-deposit-a103': [{'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': []},
                           {'op_id': 'total', 'tool': 'calculate', 'depends_on': ['transactions']}],
 'horizon-checking-combined': [{'op_id': 'whois_c101', 'tool': 'get_customer', 'depends_on': []},
                               {'op_id': 'account_c101', 'tool': 'get_account', 'depends_on': ['whois_c101']},
                               {'op_id': 'whois_c102', 'tool': 'get_customer', 'depends_on': []},
                               {'op_id': 'account_c102', 'tool': 'get_account', 'depends_on': ['whois_c102']},
                               {'op_id': 'combined',
                                'tool': 'calculate',
                                'depends_on': ['account_c101', 'account_c102']}],
 'deposit-total-combined': [{'op_id': 'txns_a101', 'tool': 'get_transactions', 'depends_on': []},
                            {'op_id': 'txns_a103', 'tool': 'get_transactions', 'depends_on': []},
                            {'op_id': 'total',
                             'tool': 'calculate',
                             'depends_on': ['txns_a101', 'txns_a103']}],
 'retrieval-multi-checking': [{'op_id': 'account_c101', 'tool': 'get_account', 'depends_on': []},
                              {'op_id': 'account_c102', 'tool': 'get_account', 'depends_on': []},
                              {'op_id': 'account_c103', 'tool': 'get_account', 'depends_on': []}]}

PROTOCOL_3_4_DRAFT = {'retrieval-bal-a103': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []}],
 'retrieval-multi-checking': [{'op_id': 'account_c101', 'tool': 'get_account', 'depends_on': []},
                              {'op_id': 'account_c102', 'tool': 'get_account', 'depends_on': []},
                              {'op_id': 'account_c103', 'tool': 'get_account', 'depends_on': []}],
 'calc-total-c101': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                     {'op_id': 'total',
                      'tool': 'calculate',
                      'depends_on': [{'op': 'accounts', 'kind': 'information', 'via': 'balances'}]}],
 'waiver-c101': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []},
                 {'op_id': 'transactions',
                  'tool': 'get_transactions',
                  'depends_on': [{'op': 'accounts',
                                  'kind': 'information',
                                  'via': 'account_id (not in the prompt)'}],
                  'necessity': 'optional'},
                 {'op_id': 'average',
                  'tool': 'calculate',
                  'depends_on': [{'op': 'accounts', 'kind': 'information', 'via': 'balances'}],
                  'necessity': 'optional'}],
 'waiver-c102': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'transactions',
                  'tool': 'get_transactions',
                  'depends_on': [{'op': 'accounts',
                                  'kind': 'information',
                                  'via': 'account_id (not in the prompt)'}]},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'waiver-c103': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                 {'op_id': 'transactions',
                  'tool': 'get_transactions',
                  'depends_on': [{'op': 'accounts',
                                  'kind': 'information',
                                  'via': 'account_id (not in the prompt)'}]},
                 {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'policy-attr-c102': [{'op_id': 'accounts', 'tool': 'get_account', 'depends_on': []},
                      {'op_id': 'transactions',
                       'tool': 'get_transactions',
                       'depends_on': [{'op': 'accounts',
                                       'kind': 'information',
                                       'via': 'account_id (not in the prompt)'}]},
                      {'op_id': 'policy', 'tool': 'search_policy', 'depends_on': []}],
 'temporal-deposit-a103': [{'op_id': 'transactions', 'tool': 'get_transactions', 'depends_on': []},
                           {'op_id': 'total',
                            'tool': 'calculate',
                            'depends_on': [{'op': 'transactions',
                                            'kind': 'information',
                                            'via': 'deposit amounts'}],
                            'necessity': 'optional'}],
 'deposit-total-combined': [{'op_id': 'txns_a101', 'tool': 'get_transactions', 'depends_on': []},
                            {'op_id': 'txns_a103', 'tool': 'get_transactions', 'depends_on': []},
                            {'op_id': 'total',
                             'tool': 'calculate',
                             'depends_on': [{'op': 'txns_a101',
                                             'kind': 'information',
                                             'via': 'deposit amounts'},
                                            {'op': 'txns_a103',
                                             'kind': 'information',
                                             'via': 'deposit amounts'}],
                             'necessity': 'optional'}],
 'horizon-checking-combined': [{'op_id': 'whois_c101',
                                'tool': 'get_customer',
                                'depends_on': [],
                                'necessity': 'obligation'},
                               {'op_id': 'whois_c102',
                                'tool': 'get_customer',
                                'depends_on': [],
                                'necessity': 'obligation'},
                               {'op_id': 'account_c101', 'tool': 'get_account', 'depends_on': []},
                               {'op_id': 'account_c102', 'tool': 'get_account', 'depends_on': []},
                               {'op_id': 'activity_a101',
                                'tool': 'get_transactions',
                                'depends_on': [{'op': 'account_c101',
                                                'kind': 'information',
                                                'via': 'account_id (not in the prompt)'}]},
                               {'op_id': 'activity_a103',
                                'tool': 'get_transactions',
                                'depends_on': [{'op': 'account_c102',
                                                'kind': 'information',
                                                'via': 'account_id (not in the prompt)'}]},
                               {'op_id': 'combined',
                                'tool': 'calculate',
                                'depends_on': [{'op': 'account_c101',
                                                'kind': 'information',
                                                'via': 'balance'},
                                               {'op': 'account_c102',
                                                'kind': 'information',
                                                'via': 'balance'}],
                                'necessity': 'optional'}]}

CANDIDATE_TABLES = {
    "working_tree_2026_11_13": WORKING_TREE_2026_11_13,
    "protocol_3_4_draft": PROTOCOL_3_4_DRAFT,
}

OPEN_QUESTIONS = {
    "waiver-c101": "is get_transactions information-necessary (protocol: no, RULE-A suffices; "
                   "required_tools and the evaluator: yes)?",
    "temporal-deposit-a103": "is calculate mandatory when the prompt never asks for it (U1/D3 calc policy)?",
    "deposit-total-combined": "same calculate question; protocol gives depth '1 or 2'",
    "horizon-checking-combined": "does 'confirm identities ... then report' create a dependency "
                                 "(draft code: yes, depth 3) or only an obligation (protocol: depth 2)? "
                                 "required_tools omits get_transactions although the prompt asks for "
                                 "30-day activity",
}


def candidate_summary(table_name, task_id):
    """Metrics of one candidate graph (see workflow.workflow_summary)."""
    graph = CANDIDATE_TABLES[table_name][task_id]
    return workflow_summary(graph, known_tools=TOOL_NAMES, label=f"{table_name}:{task_id}",
                            allow_untyped_edges=True)


def candidate_depths(table_name):
    """task_id -> primary declared depth under one candidate table."""
    return {tid: candidate_summary(table_name, tid)["primary"].depth
            for tid in CANDIDATE_TABLES[table_name]}


def disagreements():
    """task_id -> {table_name: primary depth} for tasks where the candidate
    tables give different primary depths."""
    per_table = {name: candidate_depths(name) for name in CANDIDATE_TABLES}
    out = {}
    for tid in sorted(WORKING_TREE_2026_11_13):
        depths = {name: per_table[name][tid] for name in CANDIDATE_TABLES}
        if len(set(depths.values())) > 1:
            out[tid] = depths
    return out


def working_tree_candidate_task(task_id):
    """A COPY of a legacy task with the 2026-11-13 draft graph attached as
    'operations'. The task in tasks.TASKS is not modified."""
    task = dict(get_task(task_id))
    task["operations"] = [dict(op) for op in WORKING_TREE_2026_11_13[task_id]]
    return task


def working_tree_candidate_tasks():
    return [working_tree_candidate_task(t["task_id"]) for t in TASKS]
