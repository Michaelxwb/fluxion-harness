#!/usr/bin/env python3
"""Query fake MSSP asset IP details; export all matches to CSV when requested."""

from __future__ import annotations

import csv
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from client import AssetQueryError, lookup_company, query_assets  # noqa: E402

DEFAULT_LIMIT = 20
MAX_LIMIT = 200
EXPORT_PAGE_SIZE = 200
CSV_COLUMNS = (
    "company_id",
    "company_name",
    "asset",
    "hostname",
    "business_name",
    "asset_type",
    "service_status",
    "is_alive",
    "first_time",
    "update_time",
)
Lookup = Callable[[str], str | None]
Query = Callable[..., dict[str, Any]]


def _validate(payload: object) -> tuple[list[str], str, int, int, bool, str]:
    if not isinstance(payload, dict):
        raise AssetQueryError("INPUT_INVALID", "input must be a JSON object")
    platform = str(payload.get("platform") or "mssp").strip().lower()
    if platform != "mssp":
        raise AssetQueryError(
            "PLATFORM_NOT_CONFIGURED",
            f"only the local MSSP fixture is configured; got platform={platform!r}",
        )
    raw_ids = payload.get("company_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise AssetQueryError("COMPANY_REQUIRED", "company_ids must be a non-empty array")
    company_ids: list[str] = []
    for item in raw_ids:
        if not isinstance(item, str) or not item.strip() or not item.strip().isdigit():
            raise AssetQueryError("COMPANY_INVALID", "company_ids must contain numeric strings")
        company_id = item.strip()
        if company_id not in company_ids:
            company_ids.append(company_id)
    keyword = payload.get("keyword", "")
    if not isinstance(keyword, str):
        raise AssetQueryError("INPUT_INVALID", "keyword must be a string")
    offset = payload.get("offset", 0)
    limit = payload.get("limit", DEFAULT_LIMIT)
    export = payload.get("export", False)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise AssetQueryError("INPUT_INVALID", "offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise AssetQueryError("INPUT_INVALID", f"limit must be between 1 and {MAX_LIMIT}")
    if not isinstance(export, bool):
        raise AssetQueryError("INPUT_INVALID", "export must be a boolean")
    return company_ids, keyword.strip(), offset, limit, export, platform


def _timestamp(value: object) -> str:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def _public_asset(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the tool result compact; the CSV keeps the full row."""
    return {
        "asset": row.get("asset") or "",
        "hostname": row.get("hostname") or "",
        "business_name": row.get("business_name") or "",
        "asset_type": row.get("asset_type") or "",
        "service_status": row.get("service_status"),
        "first_time": row.get("first_time"),
    }


def _write_csv(
    rows: list[dict[str, Any]], company_names: dict[str, str], output_dir: Path
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"asset-query-{'-'.join(sorted(company_names))}-{stamp}.csv"
    target = output_dir / name
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            company_id = str(row.get("_company_id") or "")
            writer.writerow(
                {
                    "company_id": company_id,
                    "company_name": company_names.get(company_id, company_id),
                    "asset": row.get("asset") or "",
                    "hostname": row.get("hostname") or "",
                    "business_name": row.get("business_name") or "",
                    "asset_type": row.get("asset_type") or "",
                    "service_status": row.get("service_status"),
                    "is_alive": row.get("is_alive"),
                    "first_time": _timestamp(row.get("first_time")),
                    "update_time": _timestamp(row.get("update_time")),
                }
            )
    return {"path": name, "rows": len(rows), "size": target.stat().st_size}


def _all_for_company(
    company_id: str, keyword: str, query: Query
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total = 0
    while True:
        page = query(company_id, offset=offset, limit=EXPORT_PAGE_SIZE, keyword=keyword)
        total = int(page.get("total") or 0)
        batch = page.get("list") if isinstance(page.get("list"), list) else []
        rows.extend(item for item in batch if isinstance(item, dict))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return rows, total


def build_result(
    payload: object,
    *,
    lookup: Lookup = lookup_company,
    query: Query = query_assets,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    company_ids, keyword, offset, limit, export, _platform = _validate(payload)
    companies: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    truncated = False
    incomplete = False
    issues: list[dict[str, Any]] = []
    company_names: dict[str, str] = {}

    for company_id in company_ids:
        name = lookup(company_id)
        if name is None:
            raise AssetQueryError("COMPANY_NOT_FOUND", f"unknown company: {company_id}")
        company_names[company_id] = name
        if export:
            rows, total = _all_for_company(company_id, keyword, query)
        else:
            page = query(company_id, offset=offset, limit=limit, keyword=keyword)
            total = int(page.get("total") or 0)
            rows = [item for item in page.get("list") or [] if isinstance(item, dict)]
            truncated = truncated or total > offset + len(rows)
        for row in rows:
            row["_company_id"] = company_id
        unique_rows: list[dict[str, Any]] = []
        company_issues: list[dict[str, Any]] = []
        if export:
            seen_ips: set[str] = set()
            duplicate_count = 0
            for row in rows:
                ip = str(row.get("asset") or "")
                if ip and ip in seen_ips:
                    duplicate_count += 1
                    continue
                seen_ips.add(ip)
                unique_rows.append(row)
            missing_count = max(0, total - len(unique_rows))
            if missing_count:
                company_issues.append({"code": "MISSING_ROWS", "count": missing_count})
            if duplicate_count:
                company_issues.append({"code": "DUPLICATE_ROWS", "count": duplicate_count})
            if company_issues:
                incomplete = True
                issues.extend({**item, "company_id": company_id} for item in company_issues)
        else:
            unique_rows = rows
        companies.append(
            {
                "company_id": company_id,
                "company_name": name,
                "total": total,
                "returned": len(unique_rows),
                "incomplete": bool(company_issues),
                "issues": company_issues,
            }
        )
        all_rows.extend(unique_rows)

    total_count = sum(int(item["total"]) for item in companies)
    if export:
        destination = output_dir or Path(os.environ.get("SKILL_OUTPUT_DIR") or ".skill-output")
        files = [_write_csv(all_rows, company_names, destination)]
        preview = [_public_asset(row) for row in all_rows[:5]]
    else:
        files = []
        preview = [_public_asset(row) for row in all_rows]
    return {
        "ok": True,
        "companies": companies,
        "total": total_count,
        "returned": len(all_rows),
        "truncated": truncated and not export,
        "incomplete": incomplete,
        "issues": issues,
        "next_offset": (
            offset + len(all_rows) if truncated and not export and len(company_ids) == 1 else None
        ),
        "assets": preview,
        "files": files,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        result = build_result(payload)
    except AssetQueryError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"error": {"code": "INPUT_INVALID", "message": str(exc)}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
