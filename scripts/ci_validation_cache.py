#!/usr/bin/env python3
"""Run one validation.yml validator and persist its content-addressed cache.

CI already runs the heavy suites in parallel. This helper makes those runs
reusable by local `code-flow task verify` / `task archive`: it executes the
validator's exact argv from `.code-flow/validation.yml` and lets the validator
engine write `.code-flow/.validation-cache.json`.

The local cache key is the validator argv plus the worktree content
fingerprint. CI must therefore run the same command on the same commit with a
clean worktree; otherwise the cache correctly misses.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".code-flow" / "scripts"))

from codeflow.core import cf_git  # noqa: E402
from codeflow.core.cf_exec_base import execution_session  # noqa: E402
from codeflow.core.cf_validators import (  # noqa: E402
    VALID,
    ValidatorConfig,
    is_heavy,
    resolve_validators,
    run_validators,
)


def select_validator(validators: Sequence[Mapping[str, object]], name: str) -> Mapping[str, object]:
    """Return the single validator named ``name``; reject missing/duplicate names."""
    matches = [item for item in validators if str(item.get("name", "")) == name]
    if not matches:
        raise ValueError(f"unknown validator: {name}")
    if len(matches) > 1:
        raise ValueError(f"duplicate validator name: {name}")
    return matches[0]


def _append_failure_log(failures: Sequence[Mapping[str, object]]) -> None:
    path = os.environ.get("PYTEST_FAILURE_LOG", "").strip()
    if not path or not failures:
        return
    try:
        with open(path, "a", encoding="utf-8") as stream:
            for failure in failures:
                detail = str(failure.get("detail", "")).replace("\n", " ")[:1000]
                stream.write(f"{failure.get('name', 'validator')}: {detail}\n")
    except OSError:
        # CI artifact logging is diagnostic; never turn a test failure into a
        # different failure just because the log path is unavailable.
        pass


def run_validator(root: str, name: str) -> dict[str, object]:
    """Execute one heavy validator and write its cache entry."""
    config, validators = resolve_validators(root)
    if config.state != VALID:
        raise ValueError(f"validation config is {config.state}: {config.error or config.path}")
    validator = select_validator(validators, name)
    if not is_heavy(validator):
        raise ValueError(f"validator is not heavy: {name}")
    files = list(cf_git.tracked_files(root))
    if not files:
        raise ValueError("no tracked files; validation cache needs a git worktree")
    with execution_session():
        outcome = run_validators(
            root,
            ValidatorConfig(VALID, (validator,)),
            files,
            "ci-validation-cache",
            strict=True,
            cache=True,
        )
    _append_failure_log(outcome.failures)
    return {
        "validator": name,
        "command": validator.get("command", ""),
        "files": len(files),
        "decision": "pass" if not outcome.failures and not outcome.truncated else "block",
        "failures": outcome.failures,
        "incomplete": outcome.truncated,
        "reused": outcome.reused,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    parser.add_argument("--validator", required=True, help="exact name from .code-flow/validation.yml")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_validator(str(Path(args.root).resolve()), args.validator)
    except (OSError, ValueError) as exc:
        print(json.dumps({"decision": "block", "validator": args.validator, "error": str(exc)},
                         ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['decision']}: {args.validator} (files={result['files']})")
    return 0 if result["decision"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
