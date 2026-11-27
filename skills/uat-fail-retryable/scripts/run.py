#!/usr/bin/env python3
"""Always fail with the same retryable error; no side effects."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(
        json.dumps(
            {
                "error": {
                    "code": "UAT_ALWAYS_FAIL",
                    "message": "uat fail retryable",
                    "retryable": True,
                }
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
