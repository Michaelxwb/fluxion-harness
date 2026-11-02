#!/usr/bin/env python3
"""Extract a source tarball into an image path.

The intranet PlatOS base image has `tar` but no `gzip`/`xz`, so the Dockerfiles
use the base Python (3.11) and its `tarfile` module instead of shelling out to
`tar -xzf`. This helper also strips the archive's single top-level directory and
merges the payload into an existing directory (needed for Node's /usr/local).

Usage:
    python extract.py <archive> <destination> [strip_components]
"""

import os
import shutil
import sys
import tarfile


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: extract.py <archive> <destination> [strip_components]", file=sys.stderr)
        return 2

    archive = sys.argv[1]
    destination = sys.argv[2]
    strip_components = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    staging = destination.rstrip("/") + ".extract"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)

    with tarfile.open(archive) as tar:
        tar.extractall(staging)

    source = staging
    for _ in range(strip_components):
        entries = os.listdir(source)
        if len(entries) != 1:
            break
        source = os.path.join(source, entries[0])

    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
