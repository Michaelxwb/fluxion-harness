#!/usr/bin/env python3
"""Merge validation cache fragments exported by CI and rebind them locally.

Each CI validator job uploads one small ``.validation-cache.json`` fragment.
Local archive needs the union of those fragments plus the entries already in
the worktree. Entries are keyed by validator argv; when the same key appears
more than once the newest ``at`` timestamp wins.

``--rebind-local`` is the CI-to-local handoff. code-flow's validator cache also
records an execution identity derived from cwd, argv, validator config,
environment and the worktree fingerprint. CI and a laptop never share cwd or
environment, so the exported entry would otherwise miss. Rebind rewrites only
that identity, keeping the CI identity/run URL/commit as provenance, and
refuses to proceed unless the local HEAD and worktree fingerprint match the CI
run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".code-flow" / "scripts"))


def load_cache(path: Path) -> dict[str, Mapping[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid validation cache {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"validation cache must be a JSON object: {path}")
    for key, entry in data.items():
        if not isinstance(key, str) or not isinstance(entry, Mapping):
            raise ValueError(f"invalid validation cache entry: {path}")
    return data


def merge_caches(paths: Iterable[Path]) -> dict[str, Mapping[str, object]]:
    merged: dict[str, Mapping[str, object]] = {}
    for path in paths:
        for key, entry in load_cache(path).items():
            current = merged.get(key)
            if current is None or str(entry.get("at", "")) >= str(current.get("at", "")):
                merged[key] = entry
    return merged


def _git(root: Path, *arguments: str) -> str:
    import subprocess

    completed = subprocess.run(
        ("git", *arguments), cwd=str(root), text=True, encoding="utf-8",
        capture_output=True, check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _provenance(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid provenance {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"provenance must be a JSON object: {path}")
    run_url = str(data.get("run_url", "")).strip()
    commit = str(data.get("commit", "")).strip()
    parsed = urlparse(run_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"provenance run_url must be an http(s) URL: {run_url!r}")
    if not commit:
        raise ValueError("provenance commit is empty")
    return run_url, commit


def _cache_key(argv: Iterable[str]) -> str:
    return hashlib.sha256(
        json.dumps({"argv": list(argv)}, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _validator_argvs(
    root: Path, validators: Iterable[Mapping[str, object]]
) -> Iterable[tuple[Mapping[str, object], list[str]]]:
    import os

    from codeflow.core import cf_git  # type: ignore[import-not-found]
    from codeflow.core.cf_exec_base import build_argv  # type: ignore[import-not-found]
    from codeflow.core.cf_validators import trigger_matches  # type: ignore[import-not-found]

    files = list(cf_git.tracked_files(str(root)))
    for validator in validators:
        matched = [
            path for path in files
            if trigger_matches(str(validator.get("trigger", "")), path)
            and os.path.isfile(root / path)
        ]
        argv = build_argv(str(validator.get("command", "")), matched)
        if argv:
            yield validator, argv


def _local_fingerprint(root: Path, validators: Iterable[Mapping[str, object]]) -> str:
    from codeflow.core.cf_exec_base import worktree_fingerprint
    from codeflow.core.cf_validators import trigger_matches

    triggers = [str(item.get("trigger", "")) for item in validators]
    untracked_filter: Callable[[str], bool] | None
    if triggers:
        def untracked_filter(path: str) -> bool:
            return any(trigger_matches(trigger, path) for trigger in triggers)
    else:
        untracked_filter = None
    return str(worktree_fingerprint(str(root), untracked_filter))


def _load_validators(root: Path) -> tuple[Mapping[str, object], ...]:
    from codeflow.core.cf_validators import VALID, resolve_validators

    config, validators = resolve_validators(str(root))
    if config.state != VALID:
        raise ValueError(f"validation config is {config.state}: {config.error or config.path}")
    return tuple(validators)


def _execution_identity(root: Path, validator: Mapping[str, object], argv: list[str],
                        fingerprint: str, entry: Mapping[str, object]) -> str:
    from codeflow.core.cf_validators import _execution_observation

    return str(_execution_observation(str(root), validator, argv, fingerprint, entry)["identity"])


def rebind_local(
    cache: dict[str, Mapping[str, object]], root: Path, run_url: str, commit: str
) -> dict[str, object]:
    """Rewrite CI execution identities to this worktree so local verify can reuse them."""
    head = _git(root, "rev-parse", "HEAD")
    if head != commit:
        raise ValueError(f"provenance commit {commit} != local HEAD {head}")
    parsed = urlparse(run_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"run_url must be an http(s) URL: {run_url!r}")
    validators = _load_validators(root)
    fingerprint = _local_fingerprint(root, validators)
    if not fingerprint:
        raise ValueError("cannot fingerprint the worktree")
    rebound = 0
    skipped = 0
    for validator, argv in _validator_argvs(root, validators):
        key = _cache_key(argv)
        entry = cache.get(key)
        if entry is None:
            continue
        if str(entry.get("fingerprint", "")) != fingerprint:
            # 本地旧缓存可能来自别的提交；保留但不能复用，等下一次真实执行刷新。
            skipped += 1
            continue
        local_identity = _execution_identity(root, validator, argv, fingerprint, entry)
        rebound_entry = dict(entry)
        rebound_entry.setdefault("ci_execution_identity", entry.get("execution_identity", ""))
        rebound_entry["ci_run_url"] = run_url
        rebound_entry["ci_commit"] = commit
        rebound_entry["source"] = "ci"
        rebound_entry["execution_identity"] = local_identity
        cache[key] = rebound_entry
        rebound += 1
    if not rebound:
        raise ValueError("no cache entries matched this commit/fingerprint; run CI on the same HEAD")
    return {"rebound": rebound, "skipped": skipped, "fingerprint": fingerprint,
            "commit": head, "run_url": run_url}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="cache fragment files")
    parser.add_argument("--output", required=True, help="merged cache path")
    parser.add_argument("--no-base", action="store_true",
                        help="do not include an existing --output file as a base")
    parser.add_argument("--rebind-local", action="store_true",
                        help="rewrite CI execution identities to this worktree")
    parser.add_argument("--provenance", default="", help="validation-cache-provenance.json")
    parser.add_argument("--run-url", default="", help="CI run URL (with --rebind-local)")
    parser.add_argument("--commit", default="", help="CI commit SHA (with --rebind-local)")
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    args = parser.parse_args(argv)
    output = Path(args.output)
    paths = [Path(item) for item in args.inputs]
    if output.is_file() and not args.no_base:
        paths.append(output)
    try:
        merged = merge_caches(paths)
    except ValueError as exc:
        print(f"merge-validation-cache: {exc}", file=sys.stderr)
        return 2
    payload: dict[str, object] = {"decision": "pass", "output": str(output), "entries": len(merged)}
    if args.rebind_local:
        try:
            run_url, commit = (_provenance(Path(args.provenance)) if args.provenance
                               else (args.run_url, args.commit))
            payload.update(rebind_local(merged, Path(args.root).resolve(), run_url, commit))
        except (OSError, ValueError) as exc:
            print(f"merge-validation-cache: {exc}", file=sys.stderr)
            return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
