"""Deterministic MiniBank environment. Owns state, nothing else."""

import copy
import json
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "bank.json"


class MiniBankEnv:
    """In-memory copy of bank.json. Never mutates the file."""

    def __init__(self, data_path=None):
        path = Path(data_path) if data_path else DEFAULT_DATA_PATH
        with open(path, encoding="utf-8") as f:
            self._data = json.load(f)

    def snapshot(self):
        return copy.deepcopy(self._data)

    def get_customer(self, customer_id):
        for c in self._data["customers"]:
            if c["customer_id"] == customer_id:
                return copy.deepcopy(c)
        return {"error": f"customer not found: {customer_id}"}

    def get_account(self, customer_id=None, account_id=None):
        accounts = self._data["accounts"]
        if account_id:
            for a in accounts:
                if a["account_id"] == account_id:
                    return [copy.deepcopy(a)]
            return {"error": f"account not found: {account_id}"}
        if customer_id:
            found = [copy.deepcopy(a) for a in accounts if a["customer_id"] == customer_id]
            if not found:
                return {"error": f"no accounts for customer: {customer_id}"}
            return found
        return {"error": "get_account requires customer_id or account_id"}

    def get_transactions(self, account_id, last_n_days=None):
        txns = [copy.deepcopy(t) for t in self._data["transactions"] if t["account_id"] == account_id]
        if last_n_days is not None:
            txns = [t for t in txns if t["days_ago"] <= last_n_days]
        return txns

    def search_policy(self, query):
        q = (query or "").lower()
        hits = []
        for p in self._data["policies"]:
            blob = f"{p['policy_id']} {p['title']} {p['text']}".lower()
            if not q or q in blob:
                hits.append(copy.deepcopy(p))
        if not hits:
            return {"error": f"no policy matches query: {query}"}
        return hits
