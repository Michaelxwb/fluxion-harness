from __future__ import annotations

from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode


def error_code_from_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code
        code = payload.get("code")
        if isinstance(code, str) and code:
            return code
    return str(ErrorCode.COMMON_INTERNAL_ERROR)


async def decode_error_payload(response: httpx.Response) -> Any:
    await response.aread()
    try:
        return response.json()
    except ValueError:
        return None


def decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def require_data_dict(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return data


def require_data_list(payload: Any) -> list[Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items
    raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
