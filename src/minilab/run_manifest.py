"""Run-start provenance snapshot ("run manifest").

Built once when a run starts (never per trial) and handed to the event sink
and, optionally, written to a file. It never influences scoring.

What it establishes, and what it refuses to claim:
  * the Git commit AND whether the working tree (within a stated scope) was
    clean. A dirty tree is never presented as a reproduction of the commit:
    the source reference then reads `...@WT#<sha8>` instead of
    `...@<commit8>#<sha8>`.
  * the identity of the source actually on disk (hash over the package's
    .py files), of the fixture, of the task definitions used, and of the
    run configuration. Canonical JSON + SHA-256; line endings normalised to
    LF before hashing so a CRLF checkout hashes like an LF one.
  * a protocol revision ONLY if the caller supplies one. None is inferred.
  * every field that cannot be determined is {"status": "unavailable",
    "reason": ...}, never a guessed value.

It contains hashes, counts, versions and short labels only: no file paths,
no file contents, no environment variables.

Reference convention (EXP-005 proposal N2, not yet owner-approved):
  name@revision#sha8        e.g. tasks@schema1#1a2b3c4d
  name@<commit8>#sha8       source identical to that commit (clean tree)
  name@WT#sha8              working-tree source that differs from the commit
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

MANIFEST_VERSION = "1"
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = PACKAGE_DIR.parents[1]
# What "clean" is judged on: the code and data a run actually uses.
DIRTY_SCOPE = ("src", "data/bank.json", "pyproject.toml")

ADAPTER_MODULES = {
    "local-stub": None, "gemini": "minilab.models.gemini", "groq": "minilab.models.groq",
    "openrouter": "minilab.models.openrouter", "omniroute": "minilab.models.omniroute",
    "local": "minilab.models.local", "openai": "minilab.models.openai",
}
ADAPTER_SDK = {"gemini": "google-genai", "groq": "openai", "openrouter": "openai",
               "omniroute": "openai", "local": "openai", "openai": "openai"}


def available(value):
    return {"status": "available", "value": value}


def unavailable(reason):
    return {"status": "unavailable", "value": None, "reason": reason}


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_object(obj) -> str:
    return sha256_hex(canonical_json(obj))


def _file_sha(path: Path) -> str:
    return sha256_hex(path.read_bytes().replace(b"\r\n", b"\n"))


def source_manifest(package_dir=PACKAGE_DIR):
    """Identity of the Python source on disk: every .py file under the
    package (no caches, no data, nothing outside the package)."""
    package_dir = Path(package_dir)
    entries = []
    for p in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        entries.append([p.relative_to(package_dir).as_posix(), _file_sha(p)])
    if not entries:
        return unavailable("no source files found")
    return available({"files": len(entries), "sha256": hash_object(entries), "line_endings": "normalized-LF"})


def git_state(repo_root=DEFAULT_REPO_ROOT, scope=DIRTY_SCOPE):
    """Commit and cleanliness, read-only (`git status` with optional locks
    off does not write the index; `git diff` is deliberately not used)."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args):
        return subprocess.run(["git", "--no-optional-locks", *args], cwd=str(repo_root), env=env,
                              capture_output=True, text=True, timeout=20)

    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            return {"commit": unavailable("not a git repository or no commit"),
                    "worktree": unavailable("git state not available")}
        status = git("status", "--porcelain", "--untracked-files=all", "--", *scope)
        if status.returncode != 0:
            return {"commit": available(head.stdout.strip()),
                    "worktree": unavailable("git status failed")}
    except Exception as e:  # noqa: BLE001 - git missing, timeout, ...
        why = f"git could not be run ({type(e).__name__})"
        return {"commit": unavailable(why), "worktree": unavailable(why)}
    lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return {"commit": available(head.stdout.strip()),
            "worktree": available({"state": "dirty" if lines else "clean",
                                   "changed_tracked_files": len(lines) - untracked,
                                   "untracked_files": untracked, "scope": list(scope)})}


def source_reference(git, source):
    """`minilab-src@<commit8>#<sha8>` only for a clean tree; `@WT#` when it
    is dirty; `@unknown#` when git state is unavailable."""
    if source["status"] != "available":
        return unavailable("source manifest unavailable")
    sha8 = source["value"]["sha256"][:8]
    commit, tree = git["commit"], git["worktree"]
    if commit["status"] != "available" or tree["status"] != "available":
        return available(f"minilab-src@unknown#{sha8}")
    if tree["value"]["state"] == "clean":
        return available(f"minilab-src@{commit['value'][:8]}#{sha8}")
    return available(f"minilab-src@WT#{sha8}")


def task_identity(tasks, schema_version):
    per_task = {t["task_id"]: hash_object(t) for t in tasks}
    digest = hash_object(sorted(per_task.items()))
    return available({"schema_version": schema_version, "count": len(per_task), "sha256": digest,
                      "ref": f"tasks@schema{schema_version}#{digest[:8]}",
                      "per_task_sha8": {k: v[:8] for k, v in sorted(per_task.items())}})


def adapter_identity(provider, model, model_version=None):
    module = ADAPTER_MODULES.get(provider, "unknown")
    sdk_name = ADAPTER_SDK.get(provider)
    if sdk_name is None:
        sdk = unavailable("no SDK: built-in offline model" if module is None else "unknown provider")
    else:
        try:
            from importlib.metadata import version
            sdk = available({"package": sdk_name, "version": version(sdk_name)})
        except Exception:  # noqa: BLE001
            sdk = unavailable(f"package {sdk_name} not installed or version not readable")
    return {"provider": provider, "model": model,
            "adapter_module": module if module else "minilab.runner (built-in)",
            "sdk": sdk,
            "model_version": available(model_version) if model_version else
            unavailable("the adapter reports no model version")}


def build_run_manifest(*, config, tasks, task_schema_version, provider, model_version=None,
                       protocol_ref=None, repo_root=DEFAULT_REPO_ROOT, package_dir=PACKAGE_DIR,
                       fixture_path=None, experiment_id=None):
    """Assemble the manifest. `config` is the run configuration dict (model,
    framework, strategy, exec_mode, temperature, seed, repeats, task_ids).
    `experiment_id` is the id every result row of this run carries; it is
    kept out of `config` so the configuration hash stays comparable."""
    git = git_state(repo_root)
    source = source_manifest(package_dir)
    fixture_path = Path(fixture_path) if fixture_path else Path(repo_root) / "data" / "bank.json"
    try:
        fixture = available({"sha256": _file_sha(fixture_path), "line_endings": "normalized-LF"})
    except OSError as e:
        fixture = unavailable(f"fixture not readable ({type(e).__name__})")
    tree = git["worktree"]
    if tree["status"] != "available":
        exact = unavailable("git working-tree state unavailable")
    else:
        exact = available(tree["value"]["state"] == "clean")
    config_sha = hash_object(config)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "experiment_id": experiment_id,
        "git": git,
        "source": source,
        "source_ref": source_reference(git, source),
        "source_is_exact_commit": exact,
        "fixture": fixture,
        "tasks": task_identity(tasks, task_schema_version),
        "config": available({"sha256": config_sha, "ref": f"config#{config_sha[:8]}"}),
        "protocol": (available({"ref": str(protocol_ref), "basis": "supplied by the caller, not verified"})
                     if protocol_ref else unavailable("no protocol revision was supplied; none is inferred")),
        "python": available({"version": platform.python_version(),
                             "implementation": platform.python_implementation()}),
        "platform": available({"system": platform.system() or "unknown"}),
        "exec_mode": available(config.get("exec_mode")) if config.get("exec_mode") else
        unavailable("not part of the configuration"),
        "adapter": adapter_identity(provider, config.get("model"), model_version),
    }
    manifest["manifest_sha256"] = hash_object(manifest)
    return manifest


def manifest_unavailable(exc, experiment_id=None):
    return {"manifest_version": MANIFEST_VERSION, "experiment_id": experiment_id, "status": "unavailable",
            "reason": f"manifest could not be built ({type(exc).__name__})"}


def write_manifest(manifest, path):
    """Write once; refuses to overwrite an existing file. Serialized before
    the file is created, and a failed write removes the partial file, so a
    failure never leaves a stray file that would block a retry."""
    data = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with open(path, "x", encoding="utf-8") as f:
        try:
            f.write(data)
            f.flush()
        except BaseException:
            f.close()
            os.remove(path)
            raise


def describe(manifest):
    """One line for logs."""
    if manifest.get("status") == "unavailable":
        return manifest["reason"]
    ref = manifest["source_ref"].get("value") or "source unavailable"
    return f"{ref} {manifest['tasks']['value']['ref']} {manifest['config']['value']['ref']} python {sys.version_info[0]}.{sys.version_info[1]}"
