"""Minimal stdlib client for the local fake MSSP asset endpoints."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "api_config.json"
DEFAULT_TIMEOUT = 30


class AssetQueryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _base_url() -> str:
    value = os.environ.get("MSSP_BASE_URL", "").strip()
    return (value or str(_config()["base_url"])).rstrip("/")


def _headers(company_id: str | None) -> dict[str, str]:
    config = _config()
    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "host": os.environ.get("MSSP_HOST_HEADER", "").strip()
        or str(config["host_header"]),
        "user-agent": "muad-asset-query-local/1.0",
        "x-requested-with": "XMLHttpRequest",
        "traceid": str(uuid.uuid4()),
        "timezone": "+08:00",
        "X-CSRFToken": os.environ.get("MSSP_CSRF_TOKEN", "").strip()
        or str(config["fixed_csrf_token"]),
        "content-type": "application/json",
    }
    if company_id:
        headers["X-MSSW-Company-Id"] = str(company_id)
    return headers


def _post(key: str, body: dict[str, Any], *, company_id: str | None = None) -> dict[str, Any]:
    endpoint = str(_config()["endpoints"][key])
    request = urllib.request.Request(
        _base_url() + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(company_id),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssetQueryError("MSSP_ASSET_QUERY_FAILED", "资产查询网络请求失败") from exc
    if status in (401, 403):
        raise AssetQueryError("MSSP_AUTH_FAILED", f"资产查询被网关拒绝（HTTP {status}）")
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetQueryError("MSSP_ASSET_QUERY_FAILED", "资产查询返回非 JSON") from exc
    if not isinstance(payload, dict):
        raise AssetQueryError("MSSP_ASSET_QUERY_FAILED", "资产查询返回结构不是对象")
    if status != 200:
        raise AssetQueryError("MSSP_ASSET_QUERY_FAILED", f"资产查询返回 HTTP {status}")
    code = payload.get("code")
    if code not in (0, "Success"):
        raise AssetQueryError(
            "MSSP_ASSET_QUERY_FAILED", f"资产查询业务码异常 code={code}"
        )
    return payload


def lookup_company(company_id: str) -> str | None:
    """Return the exact company name, or None when the fixture has no such ID."""
    payload = _post(
        "company_list",
        {"keyword": company_id, "offset": 0, "limit": 50},
        company_id=company_id,
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows = data.get("list") if isinstance(data.get("list"), list) else []
    for row in rows:
        if isinstance(row, dict) and str(row.get("company_id")) == company_id:
            return str(row.get("company_name") or company_id)
    return None


def query_assets(
    company_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
    keyword: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order": "asc",
        "offset": offset,
        "limit": limit,
        "company_id": company_id,
        "ip_url_keyword": "",
        "keyword": keyword,
        "first_time": [],
        "authorization_type": [],
        "service_status": [],
        "asset_type": [],
    }
    payload = _post("asset_query", body, company_id=company_id)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows = data.get("list") if isinstance(data.get("list"), list) else []
    try:
        total = int(data.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return {"total": total, "list": rows}
