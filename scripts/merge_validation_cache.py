#!/usr/bin/env python3
"""Merge validation cache fragments exported by CI.

Each CI validator job uploads one small `.validation-cache.json` fragment.
Local archive needs the union of those fragments plus the entries already in
the worktree. Entries are keyed by validator argv; when the same key appears
more than once the newest ``at`` timestamp wins.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable, Mapping


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="cache fragment files")
    parser.add_argument("--output", required=True, help="merged cache path")
    parser.add_argument("--no-base", action="store_true",
                        help="do not include an existing --output file as a base")
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"decision": "pass", "output": str(output), "entries": len(merged)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
