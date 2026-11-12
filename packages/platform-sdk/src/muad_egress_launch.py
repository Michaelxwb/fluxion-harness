"""Trusted file entrypoint: verify system-site hook before importing Skill code."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    import muad_egress_site

    if not os.environ.get(muad_egress_site.SOCKET_ENV) or not muad_egress_site.ready():
        print("EGRESS_HOOK_NOT_READY", file=sys.stderr)
        raise SystemExit(78)
    if len(sys.argv) < 2:
        print("EGRESS_SCRIPT_REQUIRED", file=sys.stderr)
        raise SystemExit(64)
    script = Path(sys.argv[1]).resolve()
    sys.argv = [str(script), *sys.argv[2:]]
    # Preserve ordinary Python script sibling imports under -E -s -B.
    sys.path[0] = str(script.parent)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
