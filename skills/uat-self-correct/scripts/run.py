#!/usr/bin/env python3
"""First call with an unknown customer fails retryably; the error lists the known ID."""

from __future__ import annotations

import json
import sys


def main() -> int:
    data = json.load(sys.stdin)
    customer = str(data.get("customer", "")).strip()
    if customer != "1001":
        print(
            json.dumps(
                {
                    "error": {
                        "code": "UAT_CUSTOMER_UNKNOWN",
                        "message": (
                            f"unknown customer {customer}; known customers: 1001"
                        ),
                        "retryable": True,
                    }
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {"ok": True, "customer": "1001", "assets": ["10.0.0.1", "10.0.0.2"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
