"""Canonical trajectory schema.

Task -> (Action -> Observation)* -> Final answer.

Downstream evaluation reads only the canonical fields (task_id / steps /
final_answer / termination_reason plus grounding inputs). raw_history is
optional debugging context and must never be used by the evaluator.

Provenance (schema_version, experiment_id, run_id, timestamp, model,
provider, temperature, seed, max_steps, prompt snapshot + hash) travels
with the trajectory so a trajectory file stays interpretable on its own.
"""

import json
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "1"

TERMINATION_REASONS = ("agent_final", "max_steps", "tool_error", "invalid_action", "model_error")


@dataclass
class Step:
    step: int
    action: str
    arguments: dict
    observation: object


@dataclass
class Trajectory:
    task_id: str
    steps: list = field(default_factory=list)
    final_answer: object = None
    termination_reason: str = ""
    raw_history: list = field(default_factory=list)
    # provenance (E5): part of the schema, never bolted on after serialization
    schema_version: str = SCHEMA_VERSION
    experiment_id: str = ""
    run_id: str = ""
    timestamp: str = ""
    model: str = ""
    provider: str = ""
    model_version: object = None
    temperature: float = 0.0
    seed: int = 0
    max_steps: int = 8
    prompt: str = ""
    prompt_hash: str = ""

    def to_dict(self):
        d = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "model": self.model,
            "provider": self.provider,
            "model_version": self.model_version,
            "temperature": self.temperature,
            "seed": self.seed,
            "max_steps": self.max_steps,
            "prompt": self.prompt,
            "prompt_hash": self.prompt_hash,
            "steps": [asdict(s) if isinstance(s, Step) else dict(s) for s in self.steps],
            "final_answer": self.final_answer,
            "termination_reason": self.termination_reason,
        }
        if self.raw_history:
            d["raw_history"] = self.raw_history
        return d

    @staticmethod
    def from_dict(d):
        steps = [Step(**s) for s in d.get("steps", [])]
        return Trajectory(
            task_id=d["task_id"],
            steps=steps,
            final_answer=d.get("final_answer"),
            termination_reason=d.get("termination_reason", ""),
            raw_history=d.get("raw_history", []),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            experiment_id=d.get("experiment_id", ""),
            run_id=d.get("run_id", ""),
            timestamp=d.get("timestamp", ""),
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            model_version=d.get("model_version"),
            temperature=d.get("temperature", 0.0),
            seed=d.get("seed", 0),
            max_steps=d.get("max_steps", 8),
            prompt=d.get("prompt", ""),
            prompt_hash=d.get("prompt_hash", ""),
        )


def save_jsonl(trajectories, path):
    with open(path, "a", encoding="utf-8") as f:
        for t in trajectories:
            d = t.to_dict() if isinstance(t, Trajectory) else t
            f.write(json.dumps(d, default=str) + "\n")


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(Trajectory.from_dict(json.loads(line)))
    return out
