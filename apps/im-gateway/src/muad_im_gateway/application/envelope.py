from __future__ import annotations

import logging
from typing import Any

import httpx
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


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


def error_data_from_payload(payload: Any) -> Any:
    """错误封套的 `data`（游标窗口等稳定事实）：Owner 说了什么就原样带出，不补造字段。"""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, dict) else None
    return None


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


def require_data_model[ContractT: BaseModel](
    response: httpx.Response, model: type[ContractT]
) -> ContractT:
    """真实封套 → 强类型 data。

    坏 JSON、缺 data、字段不符都显式失败为 `COMMON_INTERNAL_ERROR`；
    异常只携带稳定 code，不回显内部 URL 或响应体。
    """
    body = decode_json(response)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        logger.warning("gateway_contract_violation", extra={"contract": model.__name__, "reason": "data"})
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("gateway_contract_violation", extra={"contract": model.__name__, "reason": "fields"})
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc


def require_data_dict(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    return data

