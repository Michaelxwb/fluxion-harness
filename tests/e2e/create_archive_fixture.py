"""Expose production ZIP construction to the real browser import acceptance test."""

from __future__ import annotations

import json
import sys

from muad_agent_runtime.application.attachments.archive_tools import archive_files, build_archive


def main() -> None:
    files = json.loads(sys.argv[1])
    sys.stdout.buffer.write(build_archive(archive_files(files)))


if __name__ == "__main__":
    main()
