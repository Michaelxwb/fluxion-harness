from fastapi import Request
from muad_api import AppError
from muad_api.context import current_tenant_id
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

TENANT_HEADER = "X-Tenant-Id"


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id


def ensure_tenant_consistent(request: Request, expected_tenant_id: str) -> None:
    header_tenant_id = request.headers.get(TENANT_HEADER, "")
    if header_tenant_id and header_tenant_id != expected_tenant_id:
        raise AppError(ErrorCode.COMMON_BAD_REQUEST)
