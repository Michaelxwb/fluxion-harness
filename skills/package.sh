#!/usr/bin/env bash
# Package Skill directories under skills/ into importable zip files.
#
# Usage:
#   ./skills/package.sh                     # all skills, except _templates/
#   ./skills/package.sh smoke-platform-sample
#   ./skills/package.sh self-skill-creator self-skill-upload
#
# The zip root is flat: SKILL.md + muad.skill.json + scripts/ + references/.
# Junk is never included: __MACOSX, .DS_Store, __pycache__, *.pyc, caches, .venv,
# node_modules. Entries are sorted and carry a fixed timestamp so the same
# content produces the same bytes.
set -uo pipefail

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMES=()

if [ "$#" -gt 0 ]; then
  NAMES=("$@")
else
  for directory in "$SKILLS_DIR"/*/; do
    [ -d "$directory" ] || continue
    name="$(basename "$directory")"
    [ "$name" = "_templates" ] && continue
    NAMES+=("$name")
  done
fi

if [ "${#NAMES[@]}" -eq 0 ]; then
  echo "no skills found under $SKILLS_DIR" >&2
  exit 1
fi

python3 - "$SKILLS_DIR" "${NAMES[@]}" <<'PY'
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {
    "__MACOSX",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}
EXCLUDE_NAMES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}

root = Path(sys.argv[1])
names = sys.argv[2:]
failed = False

for name in names:
    directory = root / name
    if not directory.is_dir():
        print(f"skip {name}: not a directory", file=sys.stderr)
        failed = True
        continue
    if not (directory / "SKILL.md").is_file():
        print(f"skip {name}: missing SKILL.md", file=sys.stderr)
        failed = True
        continue
    if not (directory / "muad.skill.json").is_file():
        print(f"warn {name}: missing muad.skill.json", file=sys.stderr)

    target = root / f"{name}.zip"
    target.unlink(missing_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if any(part in EXCLUDE_DIRS for part in relative.parts):
                continue
            if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
                continue
            info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
            count += 1
    print(f"wrote {target.relative_to(root.parent)} ({count} files, {target.stat().st_size} bytes)")

raise SystemExit(1 if failed else 0)
PY
