#!/usr/bin/env python3
"""HTTP helpers for the local MSSP regression skill.

Only the Python standard library is used so the skill can be copied into a
sandbox without installing ``requests``. The request headers mirror the real
``daily-duty-report-mssp`` skill: Host routing, the fixed X-CSRFToken value and
X-MSSW-Company-Id for api-proxy calls.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
CONFIG_PATH = _ROOT / "config" / "api_config.json"
DEFAULT_TIMEOUT = 30


class FetchError(RuntimeError):
    """A read failed. The message never contains credentials or internal hosts."""


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def base_url() -> str:
    env = os.environ.get("MSSP_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    return str(load_config()["base_url"]).rstrip("/")


def host_header() -> str:
    return os.environ.get("MSSP_HOST_HEADER", "").strip() or str(
        load_config()["host_header"]
    )


def csrf_token() -> str:
    return os.environ.get("MSSP_CSRF_TOKEN", "").strip() or str(
        load_config()["fixed_csrf_token"]
    )


def endpoint(key: str) -> str:
    try:
        return str(load_config()["endpoints"][key])
    except KeyError as exc:
        raise FetchError(f"api_config.json 缺少 endpoints.{key}") from exc


def headers(company_id: str | None = None, *, json_body: bool = True) -> dict[str, str]:
    result = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "host": host_header(),
        "user-agent": "muad-local-regression-skill/0.1",
        "x-requested-with": "XMLHttpRequest",
        "traceid": str(uuid.uuid4()),
        "timezone": "+08:00",
        "X-CSRFToken": csrf_token(),
    }
    if json_body:
        result["content-type"] = "application/json"
    if company_id:
        result["X-MSSW-Company-Id"] = str(company_id)
    return result


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    company_id: str | None,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url() + path,
        data=data,
        headers=headers(company_id, json_body=body is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError("网络请求失败") from exc
    except OSError as exc:
        raise FetchError("网络请求失败") from exc

    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"接口返回非 JSON（HTTP {status}）") from exc
    if not isinstance(payload, dict):
        raise FetchError(f"接口返回结构不是对象（HTTP {status}）")
    return status, payload


def _detail(payload: dict[str, Any]) -> str:
    for key in ("msg", "message", "tip", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return ""


def post_json(
    key: str,
    body: dict[str, Any],
    *,
    company_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    check_code: bool = True,
) -> dict[str, Any]:
    status, payload = _request("POST", endpoint(key), body, company_id, timeout)
    if status in (401, 403):
        raise FetchError(f"网关拒绝访问（HTTP {status}）")
    if status != 200:
        detail = _detail(payload)
        suffix = f"（{detail}）" if detail else ""
        raise FetchError(f"接口返回 HTTP {status}{suffix}")
    if check_code:
        code = payload.get("code")
        if code not in (0, "Success"):
            detail = _detail(payload)
            suffix = f"（{detail}）" if detail else ""
            raise FetchError(f"接口返回错误 code={code}{suffix}")
    return payload


def get_json(
    key: str,
    *,
    company_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """GET that returns the JSON body even when the business code is a failure.

    ``getresponsemode`` uses ``AuthFailure.AutoDisposeAuthError`` as a normal
    "service not enabled" state, not as a transport failure.
    """
    status, payload = _request("GET", endpoint(key), None, company_id, timeout)
    if status in (401, 403):
        raise FetchError(f"网关拒绝访问（HTTP {status}）")
    if status != 200:
        detail = _detail(payload)
        suffix = f"（{detail}）" if detail else ""
        raise FetchError(f"接口返回 HTTP {status}{suffix}")
    return payload


def post_json_raw(
    key: str,
    body: dict[str, Any],
    *,
    company_id: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """POST an XDR-style endpoint that has no top-level ``code`` field."""
    return post_json(
        key,
        body,
        company_id=company_id,
        timeout=timeout,
        check_code=False,
    )


def log(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
