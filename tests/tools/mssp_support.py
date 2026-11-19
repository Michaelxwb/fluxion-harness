"""Shared helpers for MSSP-endpoint tests on the fake platform (real HTTP, no mocks)."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools/fake-business-platform/server.py"
MSSP_HOST = "soar-inner.sangfor.com.cn"
MSSP_CSRF = "6c5c52cd-3ebb-47e2-946c-15c2569836e7"
TZ = timezone(timedelta(hours=8))

PATH_COMPANY_LIST = "/order/v1/user/company_simple_info"
PATH_SERVICE_INFO = "/gateway/customer-mgr-service/order/v1/project/service_info/company_id"
PATH_CK_TABLE_QUERY = "/gateway/log-search-center-service/datalake/v1/ckTableQuery"
PATH_CK_COUNT = "/gateway/log-search-center-service/datalake/v1/ckCount"
PATH_CK_QUERY_LIST = "/gateway/log-search-center-service/datalake/v1/ckQueryList"
PATH_EVENT_TABLE = "/gateway/event-mgr/external/event_table"
PATH_EVENT_REMARK = "/gateway/event-mgr/external/event_table/remark"
PATH_EVENT_DETAIL = "/gateway/event-mgr/external/event_table/event"
PATH_EVENT_GRADING_TYPE = "/gateway/event-mgr/external/event_grading_type"
PATH_MANAGE_TYPE = "/gateway/event-mgr/external/manage_type"
PATH_ASSET_QUERY = "/gateway/asset-mgr-service/order/v1/asset"
PATH_BRANCH_DEV = "/gateway/customer-mgr-service/order/v1/branch/dev"
PATH_WHITELIST = "/order/v1/whitelist/alert_filter_list"
PATH_THREAT_RESPONSE_MODE = (
    "/gateway/api-proxy/api/apex/linkage/v1/autodispose/config/getresponsemode"
)
PATH_THREAT_STATISTICS = "/gateway/api-proxy/api/apex/linkage/v1/threatentity/statistics"
PATH_THREAT_OBJECTS = "/gateway/api-proxy/api/apex/linkage/v1/threatobjects"
PATH_WECOM_QUERY = "/gateway/flow/external/action/param/actual_data"
PATH_WECOM_MESSAGE = "/gateway/flow/external/action/wechat_group_message"
PATH_ATTACHMENT_UPLOAD = "/gateway/flow/external/attachments"
PATH_XDR_ALERT_TABLE = (
    "/gateway/api-proxy/ngsoc/INCIDENT/api/v1/table/query/alertTableQueryHandler"
)
PATH_XDR_ALERT_COUNT = (
    "/gateway/api-proxy/ngsoc/INCIDENT/api/v1/table/count/alertTableQueryHandler"
)

REPORT_DATE = "2026-11-18"
PREVIOUS_DATE = "2026-11-17"


@dataclass
class FakeMssp:
    ready: dict[str, Any]
    process: subprocess.Popen[str]
    logs: str = field(default="")

    @property
    def base_url(self) -> str:
        return str(self.ready["baseUrl"])

    @property
    def mssp_ready(self) -> dict[str, Any]:
        nested = self.ready.get("mssp")
        return nested if isinstance(nested, dict) else {}

    @property
    def host(self) -> str:
        return str(self.mssp_ready.get("hostHeader") or MSSP_HOST)

    @property
    def csrf(self) -> str:
        return str(self.mssp_ready.get("csrfToken") or MSSP_CSRF)

    def headers(
        self,
        company_id: str | None = "1001",
        *,
        host: str | None = None,
        csrf: str | None = None,
        include_company: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Host": self.host if host is None else host,
            "X-CSRFToken": self.csrf if csrf is None else csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Traceid": "test-trace",
            "Timezone": "+08:00",
            "Content-Type": "application/json",
        }
        if include_company and company_id:
            headers["X-MSSW-Company-Id"] = str(company_id)
        return headers

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        company_id: str | None = "1001",
        headers: dict[str, str] | None = None,
        timeout: float = 5,
    ) -> httpx.Response:
        return httpx.post(
            self.base_url + path,
            json=body or {},
            headers=headers or self.headers(company_id),
            timeout=timeout,
        )

    def get(
        self,
        path: str,
        *,
        company_id: str | None = "1001",
        headers: dict[str, str] | None = None,
        timeout: float = 5,
    ) -> httpx.Response:
        return httpx.get(
            self.base_url + path,
            headers=headers or self.headers(company_id, include_company=company_id is not None),
            timeout=timeout,
        )

    def control(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = httpx.post(self.base_url + path, json=body or {}, timeout=5)
        response.raise_for_status()
        return response.json()

    def stats(self) -> dict[str, Any]:
        response = httpx.get(self.base_url + "/__stats", timeout=5)
        response.raise_for_status()
        return response.json()

    def reset(self) -> None:
        self.control("/__control/reset")

    def enable_wecom(self, enabled: bool = True) -> None:
        self.control("/__control/wecom", {"enabled": enabled})

    def inject(self, **fault: Any) -> None:
        self.control("/__control/faults", fault)


@contextmanager
def running_mssp(*extra_args: str) -> Iterator[FakeMssp]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--platform",
            "smoke_platform",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            *extra_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.kill()
        process.wait(timeout=5)
        raise AssertionError(f"fake platform did not become ready: {stderr}")
    platform = FakeMssp(ready=json.loads(line), process=process)
    try:
        yield platform
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        platform.logs = process.stderr.read() if process.stderr is not None else ""


def payload(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def day_window(date_str: str) -> tuple[int, int]:
    day = datetime.fromisoformat(date_str).replace(tzinfo=TZ)
    start = day.replace(hour=0, minute=0, second=0)
    end = day.replace(hour=23, minute=59, second=59)
    return int(start.timestamp()), int(end.timestamp())


def xdr_query_body(date_str: str, *, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    start, end = day_window(date_str)
    return {
        "globalCondition": {
            "time": {
                "timeField": "lastTime",
                "begin": {"type": "absolute", "value": start},
                "end": {"type": "absolute", "value": end},
            }
        },
        "table": {"viewName": "AlertView", "pageNum": page, "pageSize": page_size},
    }


def xdr_count_body(date_str: str) -> dict[str, Any]:
    start, end = day_window(date_str)
    return {
        "globalCondition": {
            "time": {
                "timeField": "lastTime",
                "begin": {"type": "absolute", "value": start},
                "end": {"type": "absolute", "value": end},
            }
        }
    }


def event_query_body(
    company_id: str,
    date_str: str,
    *,
    offset: int = 0,
    limit: int = 100,
    event_status: list[str] | None = None,
    service_status: list[int] | None = None,
    event_name: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start, end = day_window(date_str)
    body: dict[str, Any] = {
        "order": {},
        "offset": offset,
        "limit": limit,
        "event_grading_tags": [],
        "event_status": event_status or [],
        "service_status": service_status or [],
        "event_name": event_name,
        "create_time": [start * 1000, end * 1000],
        "company_id": [str(company_id)],
    }
    body.update(extra or {})
    return body
