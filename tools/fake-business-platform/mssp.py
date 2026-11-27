#!/usr/bin/env python3
"""Fake MSSP (SOAR/data-lake) platform used by local skill regression.

The real ``daily-duty-report-mssp`` skill talks to an MSSP gateway with three
non-obvious rules that are easy to miss when mocking:

* the gateway routes by the ``Host`` header (``soar-inner.sangfor.com.cn``);
* every POST needs the platform's **fixed** ``X-CSRFToken`` value, not the
  ``csrf_token`` cookie;
* XDR requests are forwarded through ``/gateway/api-proxy`` and require
  ``X-MSSW-Company-Id``.

This module implements those rules plus the read-only endpoints the skill
uses. It is deliberately stdlib-only and returns real-looking business
payloads, including the ``code: "Success"`` form used by the apex/linkage
service and the ``code: 0`` form used by the data lake.

Nothing here talks to a network: ``server.py`` owns the HTTP loop. Tests can
inject faults through ``POST /__control/faults``; all WeCom endpoints are
disabled unless the server is started with ``--enable-wecom-write`` (or the
control endpoint enables them at runtime).
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import sys
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any

TZ = dt.timezone(dt.timedelta(hours=8))

DEFAULT_HOST_HEADER = "soar-inner.sangfor.com.cn"
FIXED_CSRF_TOKEN = "6c5c52cd-3ebb-47e2-946c-15c2569836e7"
DEFAULT_COMPANY_ID = "1001"
FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "mssp.json"

CODE_SUCCESS = 0
CODE_PARAMS_ERR = 1001
CODE_AUTH_FAILED = 1002
CODE_ROUTE_HEADER_MISSING = 1003
CODE_BUSY = 9064
CODE_CK_PARAMS = 9001
CODE_ILLEGAL_OPERATION = 9451
CODE_INTERNAL = 1005
WECOM_DISABLED_CODE = 40300

PATH_COMPANY_LIST = "/order/v1/user/company_simple_info"
PATH_SERVICE_INFO = "/gateway/customer-mgr-service/order/v1/project/service_info/company_id"
PATH_CK_TABLE_QUERY = "/gateway/log-search-center-service/datalake/v1/ckTableQuery"
PATH_CK_COUNT = "/gateway/log-search-center-service/datalake/v1/ckCount"
PATH_CK_QUERY_LIST = "/gateway/log-search-center-service/datalake/v1/ckQueryList"
PATH_CK_QUERY = "/gateway/log-search-center-service/datalake/v1/ckQuery"
PATH_EVENT_TABLE = "/gateway/event-mgr/external/event_table"
PATH_EVENT_REMARK = "/gateway/event-mgr/external/event_table/remark"
PATH_EVENT_DETAIL = "/gateway/event-mgr/external/event_table/event"
PATH_EVENT_GRADING_TYPE = "/gateway/event-mgr/external/event_grading_type"
PATH_MANAGE_TYPE = "/gateway/event-mgr/external/manage_type"
PATH_WHITELIST = "/order/v1/whitelist/alert_filter_list"
PATH_ASSET_QUERY = "/gateway/asset-mgr-service/order/v1/asset"
PATH_BRANCH_DEV = "/gateway/customer-mgr-service/order/v1/branch/dev"
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

ENDPOINTS: dict[str, str] = {
    "company_list": PATH_COMPANY_LIST,
    "service_info": PATH_SERVICE_INFO,
    "ck_table_query": PATH_CK_TABLE_QUERY,
    "ck_count": PATH_CK_COUNT,
    "ck_query_list": PATH_CK_QUERY_LIST,
    "ck_query": PATH_CK_QUERY,
    "event_table": PATH_EVENT_TABLE,
    "event_remark": PATH_EVENT_REMARK,
    "event_detail": PATH_EVENT_DETAIL,
    "event_grading_type": PATH_EVENT_GRADING_TYPE,
    "manage_type": PATH_MANAGE_TYPE,
    "whitelist_list": PATH_WHITELIST,
    "asset_query": PATH_ASSET_QUERY,
    "branch_dev": PATH_BRANCH_DEV,
    "threat_response_mode": PATH_THREAT_RESPONSE_MODE,
    "threat_entity_statistics": PATH_THREAT_STATISTICS,
    "threat_objects": PATH_THREAT_OBJECTS,
    "wechat_group_query": PATH_WECOM_QUERY,
    "wechat_group_message": PATH_WECOM_MESSAGE,
    "attachment_upload": PATH_ATTACHMENT_UPLOAD,
    "xdr_alert_table": PATH_XDR_ALERT_TABLE,
    "xdr_alert_count": PATH_XDR_ALERT_COUNT,
}
PATH_TO_KEY = {path: key for key, path in ENDPOINTS.items()}

# Real data-lake table ids are stable per deployment; the fake keeps fixed ids
# so tests can assert that the skill resolves tables by name, not by number.
TABLES: tuple[dict[str, Any], ...] = (
    {"id": 6408, "tableName": "网络安全日志", "tableType": "log"},
    {"id": 6409, "tableName": "终端安全日志", "tableType": "log"},
    {"id": 6410, "tableName": "XDR告警表", "tableType": "alert"},
)

MANAGE_TYPE_NAMES = {
    "EMERGENCY_RESPONSE": "应急工单",
    "INTRANET_THREAT": "内部威胁",
    "INTERNET_THREAT": "外部威胁",
    "VULNERABILITY": "脆弱性",
    "OTHER": "其他",
    "UNDECLARED_THREAT": "未公开威胁",
    "MANAGEMENT": "管理要求",
    "STRATEGY_OPTIMIZE": "策略调优",
    "STRATEGY_SERVICE": "策略工单",
    "CUSTOMER_FEEDBACK": "用户反馈",
}
EVENT_STATUS_NAMES = {
    "inited": "未处置",
    "disposal": "处置中",
    "suspend": "定期跟进",
    "finished": "已闭环",
    "accept_risk": "不处置",
    "announced": "已通告",
    "protected": "已防护",
}
RISK_NAMES = {"high": "高危", "medium": "中危", "low": "低危"}
RISK_CODES = {"high": 3, "medium": 2, "low": 1}
GEO_BY_SOURCE = {
    "203.0.113.10": {"countryName": "美国", "provinceName": "", "cityName": ""},
    "203.0.113.99": {"countryName": "美国", "provinceName": "", "cityName": ""},
    "198.51.100.20": {"countryName": "中国", "provinceName": "江苏省", "cityName": "南京市"},
}


class MsspFixtureError(RuntimeError):
    """The fixture file is missing or malformed; startup must fail loudly."""


class MsspBadRequest(ValueError):
    """Business-parameter error that should return an MSSP business code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MsspResponse:
    status: int
    payload: dict[str, Any]
    headers: tuple[tuple[str, str], ...] = ()


def load_fixtures(path: str | Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the deterministic fixture file."""
    fixture_path = Path(path) if path else FIXTURES_PATH
    try:
        raw = fixture_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MsspFixtureError(f"无法读取 MSSP fixtures：{fixture_path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MsspFixtureError(f"MSSP fixtures 不是合法 JSON：{fixture_path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("companies"), dict):
        raise MsspFixtureError("MSSP fixtures 缺少 companies 对象")
    return data


def parse_iso(value: Any) -> int | None:
    """ISO timestamp → epoch seconds. Accepts numbers too (already seconds)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return int(parsed.timestamp())


def format_epoch(epoch: int | float | None, *, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if epoch is None:
        return ""
    try:
        return dt.datetime.fromtimestamp(int(epoch), TZ).strftime(fmt)
    except (OSError, OverflowError, TypeError, ValueError):
        return ""


def format_date_from_epoch(epoch: int | float) -> str:
    return format_epoch(epoch, fmt="%Y-%m-%d")


def success(data: dict[str, Any] | None = None, **extra: Any) -> MsspResponse:
    payload: dict[str, Any] = {"code": CODE_SUCCESS}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return MsspResponse(200, payload)


def business_error(code: int, message: str, *, status: int = 200) -> MsspResponse:
    return MsspResponse(status, {"code": code, "msg": message})


def linkage_success(data: dict[str, Any]) -> MsspResponse:
    return MsspResponse(200, {"code": "Success", "data": data})


class MsspService:
    """Pure dispatcher: HTTP headers in, ``MsspResponse`` out."""

    def __init__(
        self,
        fixtures_path: str | Path | None = None,
        *,
        host_header: str = DEFAULT_HOST_HEADER,
        csrf_token: str = FIXED_CSRF_TOKEN,
        default_company: str = DEFAULT_COMPANY_ID,
        wecom_write: bool = False,
    ):
        self.fixtures = load_fixtures(fixtures_path)
        self.host_header = host_header
        self.csrf_token = csrf_token
        self.default_company = default_company
        self.wecom_write = wecom_write
        self.request_counts: dict[str, int] = {}
        self.faults: list[dict[str, Any]] = []
        self.faults_served = 0
        self.uploads: dict[str, dict[str, Any]] = {}
        self.sent: dict[str, dict[str, Any]] = {}
        self.duplicate_sends = 0
        self._remarks: dict[str, str] = {}
        self._report_date = str(self.fixtures.get("report_date") or "2026-11-18")

    # ------------------------------------------------------------------ #
    # HTTP entry
    # ------------------------------------------------------------------ #

    def handle(
        self,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> MsspResponse:
        del query
        try:
            if path.startswith("/__"):
                return self._handle_control(method, path, headers, body)
            return self._handle_business(method, path, headers, body)
        except MsspBadRequest as exc:
            return business_error(exc.code, exc.message)
        except Exception:  # noqa: BLE001 - a fake must not take down the server
            traceback.print_exc(file=sys.stderr)
            return business_error(CODE_INTERNAL, "服务内部错误", status=500)

    def _handle_business(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> MsspResponse:
        if not self._host_allowed(headers):
            return business_error(4004, f"unknown host: {headers.get('host', '')}", status=404)
        if method == "POST" and not self._csrf_allowed(headers):
            return business_error(CODE_ILLEGAL_OPERATION, "非法操作", status=403)

        key = PATH_TO_KEY.get(path)
        if key is None:
            return business_error(4004, "not found", status=404)
        if path.startswith("/gateway/api-proxy/") and not headers.get("x-mssw-company-id"):
            return business_error(
                CODE_ROUTE_HEADER_MISSING, "apiproxy缺失必要路由头", status=400
            )

        payload = (
            self._json_body(body)
            if method == "POST" and key != "attachment_upload"
            else {}
        )
        company_id = self._resolve_company_id(headers, payload)
        self.request_counts[path] = self.request_counts.get(path, 0) + 1
        fault = self._fault_for(key, path, company_id)
        if fault is not None:
            return fault
        return self._dispatch(key, payload, headers, body, company_id)

    def _dispatch(
        self,
        key: str,
        payload: dict[str, Any],
        headers: Mapping[str, str],
        raw_body: bytes,
        company_id: str,
    ) -> MsspResponse:
        company = self._company(company_id)
        handlers = {
            "company_list": lambda: self._company_list(payload),
            "service_info": lambda: self._service_info(payload, company),
            "ck_table_query": lambda: self._ck_table_query(),
            "ck_count": lambda: self._ck_count(payload, company),
            "ck_query_list": lambda: self._ck_query_list(payload, company),
            "ck_query": lambda: business_error(9002, "权限不足"),
            "event_table": lambda: self._event_table(payload, company),
            "event_remark": lambda: self._event_remark(payload, company),
            "event_detail": lambda: self._event_detail(payload, company),
            "event_grading_type": lambda: self._event_grading_type(payload, company),
            "manage_type": lambda: self._manage_type(payload, company),
            "whitelist_list": lambda: self._whitelist_list(payload, company),
            "asset_query": lambda: self._asset_query(payload, company),
            "branch_dev": lambda: self._branch_dev(payload, company),
            "threat_response_mode": lambda: self._threat_response_mode(company),
            "threat_entity_statistics": lambda: self._threat_statistics(company),
            "threat_objects": lambda: self._threat_objects(payload, company),
            "wechat_group_query": lambda: self._wecom_query(company),
            "wechat_group_message": lambda: self._wecom_send(payload, headers),
            "attachment_upload": lambda: self._wecom_upload(headers, raw_body),
            "xdr_alert_table": lambda: self._xdr_alert_table(payload, company),
            "xdr_alert_count": lambda: self._xdr_alert_count(payload, company),
        }
        return handlers[key]()

    # ------------------------------------------------------------------ #
    # Auth / routing / fixtures
    # ------------------------------------------------------------------ #

    def _host_allowed(self, headers: Mapping[str, str]) -> bool:
        host = str(headers.get("host") or "").strip()
        if not host:
            return False
        return host.split(":", 1)[0].lower() == self.host_header.lower()

    def _csrf_allowed(self, headers: Mapping[str, str]) -> bool:
        provided = str(headers.get("x-csrftoken") or headers.get("x-csrf-token") or "")
        return provided == self.csrf_token

    def _resolve_company_id(
        self, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> str:
        candidates: list[Any] = [payload.get("company_id")]
        for param in payload.get("param_list") or []:
            if isinstance(param, dict):
                other = param.get("other_param") or {}
                if isinstance(other, dict):
                    candidates.append(other.get("company_id"))
        for candidate in candidates:
            if isinstance(candidate, list) and candidate:
                candidate = candidate[0]
            text = str(candidate or "").strip()
            if text:
                return text
        header = str(headers.get("x-mssw-company-id") or "").strip()
        if header:
            return header
        return self.default_company

    def _company(self, company_id: str) -> dict[str, Any]:
        cid = str(company_id or "").strip() or self.default_company
        defaults = copy.deepcopy(self.fixtures.get("defaults") or {})
        override = (self.fixtures.get("companies") or {}).get(cid)
        if not isinstance(override, dict):
            return {
                "company_id": cid,
                "name": f"未知客户（{cid}）",
                "full_asset": False,
                "logs": {"network": 0, "endpoint": 0},
                "services": [],
                "events": [],
                "alerts": {"default": {"count": 0}},
                "entities": {
                    "mode": "security",
                    "stats": {"total": 0, "banned": 0, "allowed": 0, "monitored": 0},
                },
                "whitelist": [],
                "devices": [],
                "assets": [],
            }
        merged = defaults
        for key, value in override.items():
            if (
                key in ("logs", "entities", "alerts")
                and isinstance(value, dict)
                and isinstance(merged.get(key), dict)
            ):
                merged[key] = {**merged[key], **copy.deepcopy(value)}
            else:
                merged[key] = copy.deepcopy(value)
        merged["company_id"] = cid
        merged.setdefault("name", cid)
        return merged

    def _json_body(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MsspBadRequest(CODE_PARAMS_ERR, "请求体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise MsspBadRequest(CODE_PARAMS_ERR, "请求体必须是 JSON 对象")
        return payload

    # ------------------------------------------------------------------ #
    # Company / service / data lake
    # ------------------------------------------------------------------ #

    def _company_list(self, payload: dict[str, Any]) -> MsspResponse:
        keyword = str(payload.get("keyword") or "").strip().lower()
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, int(payload.get("limit") or 100))
        rows = []
        for cid, fixture in (self.fixtures.get("companies") or {}).items():
            name = str(fixture.get("name") or cid)
            if keyword and keyword not in name.lower() and keyword not in str(cid):
                continue
            rows.append(
                {
                    "company_id": str(cid),
                    "company_name": name,
                    "company_simple_name": name,
                    "status": 1,
                }
            )
        return success({"total": len(rows), "list": rows[offset : offset + limit]})

    def _service_info(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, int(payload.get("limit") or 100))
        rows = []
        for item in company.get("services") or []:
            rows.append(
                {
                    "service_name": item.get("service_name") or "安全托管服务",
                    "main_service_code": item.get("main_service_code") or "",
                    "service_code": item.get("service_code") or "",
                    "auth_status": item.get("auth_status", 1),
                    "service_status": item.get("service_status", "running"),
                    "start_time": item.get("start_time") or "",
                    "end_time": item.get("end_time") or "",
                }
            )
        return success({"total": len(rows), "list": rows[offset : offset + limit]})

    def _ck_table_query(self) -> MsspResponse:
        return success({"list": [dict(table) for table in TABLES]})

    def _ck_count(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        table_ids = payload.get("tableId") or []
        if not isinstance(table_ids, list):
            raise MsspBadRequest(CODE_PARAMS_ERR, "tableId 必须是数组")
        logs = company.get("logs") or {}
        counts = {
            6408: int(logs.get("network") or 0),
            6409: int(logs.get("endpoint") or 0),
            6410: self._alert_count_for_window(payload, company),
        }
        total = sum(counts.get(int(table_id), 0) for table_id in table_ids)
        return success({"total": total})

    def _ck_query_list(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        for required in ("pageNo", "pageSize", "fieldList"):
            if required not in payload:
                raise MsspBadRequest(CODE_CK_PARAMS, f"缺少参数 {required}")
        if "isAgg" in payload or "groupBy" in payload:
            raise MsspBadRequest(CODE_CK_PARAMS, "不支持聚合查询")
        page_no = max(0, int(payload.get("pageNo") or 0))
        page_size = max(1, min(500, int(payload.get("pageSize") or 100)))
        events = self._all_events(company)
        start = page_no * page_size
        rows = []
        for row in events[start : start + page_size]:
            rows.append(
                {
                    "log_time": row.get("create_time"),
                    "customer": company["company_id"],
                    "src_ip": row.get("src_ip") or "203.0.113.10",
                    "dst_ip": row.get("host_ip"),
                    "event_name": row.get("event_name"),
                    "raw": {"event_id": row.get("event_id")},
                }
            )
        return success(
            {
                "condiResults": rows,
                "tableFields": [
                    {"fieldName": "log_time", "fieldAlias": "时间"},
                    {"fieldName": "customer", "fieldAlias": "客户"},
                    {"fieldName": "src_ip", "fieldAlias": "源IP"},
                    {"fieldName": "dst_ip", "fieldAlias": "目的IP"},
                    {"fieldName": "event_name", "fieldAlias": "事件名称"},
                ],
            }
        )

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def _event_table(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        for required in ("order", "offset", "limit", "event_grading_tags"):
            if required not in payload:
                raise MsspBadRequest(CODE_BUSY, f"缺少参数 {required}")
        limit = int(payload.get("limit") or 0)
        offset = int(payload.get("offset") or 0)
        if limit not in (10, 20, 100):
            raise MsspBadRequest(CODE_BUSY, "limit 只接受 10/20/100")
        if offset < 0 or offset % limit:
            raise MsspBadRequest(CODE_BUSY, "offset 必须是 limit 的整数倍")
        rows = self._filtered_events(payload, company)
        return success({"total": len(rows), "list": rows[offset : offset + limit]})

    def _event_remark(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        for required in ("order", "offset", "limit", "event_id"):
            if required not in payload:
                raise MsspBadRequest(CODE_PARAMS_ERR, f"缺少参数 {required}")
        event_id = str(payload.get("event_id") or "").strip()
        if not self._find_event(company, event_id):
            return success({"total": 0, "list": []})
        remark = self._remarks.get(event_id)
        if not remark:
            return success({"total": 0, "list": []})
        return success(
            {
                "total": 1,
                "list": [
                    {
                        "event_id": event_id,
                        "event_status": "accept_risk",
                        "remark_txt": f"<p>{remark}</p>",
                        "create_time": format_epoch(self._fixed_now()),
                        "user_name": "fake-operator",
                    }
                ],
            }
        )

    def _event_detail(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        event_id = str(payload.get("event_id") or "").strip()
        row = self._find_event(company, event_id)
        return success({"base_info": row or {}})

    def _event_grading_type(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        if any(key in payload for key in ("event_grading_tags", "order", "offset", "limit")):
            raise MsspBadRequest(CODE_BUSY, "该接口不接受分页或筛选参数")
        counts: dict[str, int] = {}
        for row in self._all_events(company):
            counts[row["risk_level"]] = counts.get(row["risk_level"], 0) + 1
        rows = [
            {
                "event_grading_type": level,
                "event_grading_name": RISK_NAMES[level],
                "count": counts[level],
            }
            for level in ("high", "medium", "low")
            if counts.get(level)
        ]
        return success({"total": sum(counts.values()), "list": rows})

    def _manage_type(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        if "manage_type" in payload or any(
            key in payload for key in ("order", "offset", "limit")
        ):
            raise MsspBadRequest(CODE_BUSY, "该接口不接受 manage_type 或分页参数")
        counts: dict[str, int] = {}
        for row in self._all_events(company):
            manage_type = row["manage_type"]
            counts[manage_type] = counts.get(manage_type, 0) + 1
        rows = [
            {
                "manage_type": key,
                "manage_type_name": MANAGE_TYPE_NAMES.get(key, key),
                "count": value,
            }
            for key, value in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        return success({"total": sum(counts.values()), "list": rows})

    def _all_events(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        return self._expanded_events(company, {})

    def _expanded_events(
        self, company: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        plans = list(company.get("events") or [])
        total = sum(max(1, int(plan.get("count") or 1)) for plan in plans)
        window = payload.get("create_time")
        if isinstance(window, list) and len(window) == 2:
            start_ms, end_ms = int(window[0]), int(window[1])
        else:
            start_ms = parse_iso(f"{self._report_date}T00:00:00+08:00") * 1000
            end_ms = parse_iso(f"{self._report_date}T23:59:59+08:00") * 1000
        span = max(1, end_ms - start_ms)
        rows: list[dict[str, Any]] = []
        index = 0
        for plan in plans:
            count = max(1, int(plan.get("count") or 1))
            for _ in range(count):
                event_id = f"evt-{company['company_id']}-{index:04d}"
                ts = start_ms + span * (index + 1) // (total + 1)
                risk = str(plan.get("risk_level") or "medium")
                host_ip = str(plan.get("host_ip") or "10.10.1.11")
                row = {
                    "event_id": event_id,
                    "event_name": str(plan.get("event_name") or "安全事件"),
                    "event_status": str(plan.get("event_status") or "finished"),
                    "manage_type": str(plan.get("manage_type") or "OTHER"),
                    "manage_sub_type": str(plan.get("manage_sub_type") or ""),
                    "service_status": int(plan.get("service_status") or 0),
                    "push_tag": int(plan.get("push_tag") or 0),
                    "risk_level": risk,
                    "event_grading_tags": [],
                    "host_ip": host_ip,
                    "real_ip": str(plan.get("real_ip") or host_ip),
                    "hostname": "",
                    "dev_name": str(plan.get("dev_name") or ""),
                    "business_name": f"{company.get('name')}>业务{index % 3 + 1}",
                    "company_id": [company["company_id"]],
                    "create_time": format_epoch(ts // 1000),
                    "latest_time": format_epoch(ts // 1000),
                    "source": "fake-mssp",
                }
                remark = plan.get("remark")
                if remark:
                    self._remarks[event_id] = str(remark)
                rows.append(row)
                index += 1
        return rows

    def _filtered_events(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rows = self._expanded_events(company, payload)
        company_filter = payload.get("company_id")
        if company_filter:
            allowed = {str(item) for item in company_filter}
            if company["company_id"] not in allowed:
                return []
        statuses = {str(item) for item in payload.get("event_status") or []}
        if statuses:
            rows = [row for row in rows if row["event_status"] in statuses]
        service_status = payload.get("service_status") or []
        if service_status:
            allowed = {int(item) for item in service_status}
            rows = [row for row in rows if row["service_status"] in allowed]
        event_name = str(payload.get("event_name") or "").strip()
        if event_name:
            rows = [row for row in rows if event_name in row["event_name"]]
        manage_type = payload.get("manage_type")
        if manage_type:
            rows = [row for row in rows if _manage_type_matches(row, manage_type)]
        return rows

    def _find_event(
        self, company: dict[str, Any], event_id: str
    ) -> dict[str, Any] | None:
        for row in self._all_events(company):
            if row["event_id"] == event_id:
                return row
        return None

    # ------------------------------------------------------------------ #
    # Assets / devices / whitelist
    # ------------------------------------------------------------------ #

    def _asset_query(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        assets = self._all_assets(company)
        keyword = str(payload.get("ip_url_keyword") or "").strip()
        if keyword:
            assets = [row for row in assets if keyword in row["asset"]]
        first_time = payload.get("first_time") or []
        if isinstance(first_time, list) and len(first_time) == 2:
            start_ms, end_ms = int(first_time[0]), int(first_time[1])
            assets = [
                row
                for row in assets
                if start_ms <= int(row["_first_time_sec"]) * 1000 <= end_ms
            ]
        auth_types = payload.get("authorization_type") or []
        if auth_types:
            allowed = {int(item) for item in auth_types}
            assets = [
                row
                for row in assets
                if allowed & {int(item) for item in row["authorization_type"]}
            ]
        service_status = payload.get("service_status") or []
        if service_status:
            allowed = {int(item) for item in service_status}
            assets = [row for row in assets if int(row["service_status"]) in allowed]
        asset_types = payload.get("asset_type") or []
        if asset_types:
            allowed = {str(item) for item in asset_types}
            assets = [row for row in assets if row["asset_type"] in allowed]
        asset_keyword = str(payload.get("keyword") or "").strip()
        if asset_keyword:
            assets = [
                row
                for row in assets
                if asset_keyword in row["asset"]
                or asset_keyword in row["business_name"]
                or asset_keyword in row["hostname"]
            ]
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, int(payload.get("limit") or 10))
        fault = company.get("asset_page_fault")
        drop_positions: set[int] = set()
        duplicate_offset = -1
        if isinstance(fault, dict):
            duplicate_offset = int(fault.get("duplicate_offset", -1))
            drop_positions = {int(item) for item in fault.get("drop_positions") or []}
        page = []
        for index in range(offset, min(offset + limit, len(assets))):
            if index in drop_positions:
                continue
            page.append(
                {key: value for key, value in assets[index].items() if key != "_first_time_sec"}
            )
        if duplicate_offset == offset and 0 <= offset < len(assets):
            page.insert(
                0,
                {key: value for key, value in assets[offset].items() if key != "_first_time_sec"},
            )
        return success({"total": len(assets), "list": page})

    def _all_assets(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        explicit: dict[str, dict[str, Any]] = {}
        for item in company.get("assets") or []:
            ip = self._asset_ip(item)
            if not ip:
                continue
            first_time = parse_iso(item.get("first_time")) or parse_iso("2025-01-01T00:00:00+08:00")
            explicit[ip] = {
                "_id": str(item.get("_id") or item.get("asset_id") or f"asset-{ip}"),
                "asset_id": str(item.get("asset_id") or item.get("_id") or f"asset-{ip}"),
                "asset": ip,
                "business_name": str(item.get("business_name") or ""),
                "hostname": str(item.get("hostname") or ""),
                "asset_group_name": str(item.get("asset_group_name") or "默认资产组"),
                "asset_type": str(item.get("asset_type") or "server"),
                "authorization_type": [
                    int(value) for value in item.get("authorization_type") or [1, 2]
                ],
                "service_status": int(item.get("service_status") or 1),
                "is_alive": int(item.get("is_alive", 1)),
                "first_time": first_time,
                "update_time": parse_iso(item.get("update_time")) or first_time,
                "_first_time_sec": first_time,
            }
        ips: list[str] = []
        for row in self._all_events(company):
            ip = str(row.get("host_ip") or "").strip()
            if ip and ip not in ips:
                ips.append(ip)
        for ip in self._alert_ips(company):
            if ip and ip not in ips:
                ips.append(ip)
        for index, ip in enumerate(ips):
            if ip in explicit:
                continue
            first_time = parse_iso("2025-01-01T00:00:00+08:00")
            explicit[ip] = {
                "_id": f"asset-{company['company_id']}-{index:03d}",
                "asset_id": f"asset-{company['company_id']}-{index:03d}",
                "asset": ip,
                "business_name": f"{company.get('name')}业务{index % 3 + 1}",
                "hostname": f"host-{ip.replace('.', '-')}",
                "asset_group_name": "默认资产组",
                "asset_type": "server",
                "authorization_type": [1, 2],
                "service_status": 1,
                "is_alive": 1,
                "first_time": first_time,
                "update_time": first_time,
                "_first_time_sec": first_time,
            }
        for row in self._bulk_assets(company):
            explicit.setdefault(row["asset"], row)
        return list(explicit.values())

    def _bulk_assets(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        """Deterministically expand ``asset_bulk`` fixtures.

        The daily-report fixtures stay small; this knob gives the asset-query
        acceptance a customer large enough to force truncation and file export
        without shipping a thousand literal rows in JSON.
        """
        spec = company.get("asset_bulk")
        if not isinstance(spec, dict):
            return []
        try:
            count = int(spec.get("count") or 0)
        except (TypeError, ValueError):
            return []
        if count <= 0:
            return []
        prefix = str(spec.get("prefix") or "10.99").strip().strip(".")
        if not prefix:
            return []
        first_time = parse_iso("2025-01-01T00:00:00+08:00") or 0
        rows: list[dict[str, Any]] = []
        for index in range(count):
            # 10.60.0.1 .. 10.60.4.184 for the default 1200-row fixture.
            third = (index // 254) % 254
            fourth = index % 254 + 1
            ip = f"{prefix}.{third}.{fourth}"
            rows.append(
                {
                    "_id": f"asset-{company['company_id']}-bulk-{index:04d}",
                    "asset_id": f"asset-{company['company_id']}-bulk-{index:04d}",
                    "asset": ip,
                    "business_name": f"{company.get('name')}批量业务{index % 5 + 1}",
                    "hostname": f"bulk-host-{index:04d}",
                    "asset_group_name": "批量资产组",
                    "asset_type": "server",
                    "authorization_type": [1, 2],
                    "service_status": 1,
                    "is_alive": 1,
                    "first_time": first_time,
                    "update_time": first_time,
                    "_first_time_sec": first_time,
                }
            )
        return rows

    def _asset_ip(self, item: dict[str, Any]) -> str:
        value = item.get("asset") or item.get("ip")
        if isinstance(value, list):
            return str(value[0]).strip() if value else ""
        return str(value or "").strip()

    def _branch_dev(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        rows = []
        for item in company.get("devices") or []:
            create_time = parse_iso(item.get("create_time")) or self._fixed_now()
            rows.append(
                {
                    "dev_id": str(item.get("dev_id") or f"dev-{item.get('gateway_id')}"),
                    "gateway_id": str(item.get("gateway_id") or ""),
                    "dev_name": str(item.get("dev_name") or ""),
                    "dev_type": int(item.get("dev_type") or 0),
                    "type_name": str(item.get("type_name") or ""),
                    "dev_status": int(item.get("dev_status") or 1),
                    "platform_status": str(item.get("platform_status") or "online"),
                    "branch_name": str(item.get("branch_name") or ""),
                    "create_time": create_time,
                }
            )
        dev_type = payload.get("type")
        if dev_type not in (None, "", 0):
            rows = [row for row in rows if row["dev_type"] == int(dev_type)]
        status = int(payload.get("status", 0) or 0)
        if status:
            rows = [row for row in rows if row["dev_status"] == status]
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, int(payload.get("limit") or 100))
        return success({"total": len(rows), "list": rows[offset : offset + limit]})

    def _whitelist_list(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        rows = []
        for item in company.get("whitelist") or []:
            valid_time = parse_iso(item.get("valid_time"))
            if valid_time is None:
                continue
            rows.append(
                {
                    "_id": str(item.get("_id") or f"wl-{company['company_id']}"),
                    "name": str(item.get("name") or ""),
                    "valid_time": format_date_from_epoch(valid_time),
                    "end_time": format_epoch(parse_iso(item.get("end_time")), fmt="%Y-%m-%d"),
                    "online": int(item.get("online") or 0),
                    "hit_count": int(item.get("hit_count") or 0),
                    "_valid_ts": valid_time,
                }
            )
        window = payload.get("valid_time") or []
        if isinstance(window, list) and len(window) == 2:
            start_ms, end_ms = int(window[0]), int(window[1])
            rows = [
                row for row in rows if start_ms <= row["_valid_ts"] * 1000 <= end_ms
            ]
        offset = max(0, int(payload.get("offset") or 0))
        limit = max(1, int(payload.get("limit") or 100))
        page = [
            {key: value for key, value in row.items() if key != "_valid_ts"}
            for row in rows[offset : offset + limit]
        ]
        return success({"total": len(rows), "list": page})

    # ------------------------------------------------------------------ #
    # Threat entities (apex / linkage)
    # ------------------------------------------------------------------ #

    def _threat_response_mode(self, company: dict[str, Any]) -> MsspResponse:
        entities = company.get("entities") or {}
        mode = str(entities.get("mode") or "security").strip().lower()
        if mode == "disabled":
            return MsspResponse(
                200,
                {
                    "code": "AuthFailure.AutoDisposeAuthError",
                    "message": "当前账号没有该功能权限",
                },
            )
        return linkage_success({"responseMode": mode})

    def _threat_statistics(self, company: dict[str, Any]) -> MsspResponse:
        entities = company.get("entities") or {}
        stats = entities.get("stats") or {}
        return linkage_success(
            {
                "totalCount": int(stats.get("total") or 0),
                "blockedCount": int(stats.get("banned") or 0),
                "unblockedCount": int(stats.get("allowed") or 0),
                "observedCount": int(stats.get("monitored") or 0),
            }
        )

    def _threat_objects(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        page_size = int(payload.get("pageSize") or 100)
        if page_size > 100:
            return MsspResponse(
                200,
                {
                    "code": "InternalError.InvalidParameter",
                    "message": "pageSize 不能大于 100",
                },
            )
        page_num = max(1, int(payload.get("pageNum") or 1))
        rows = self._entity_objects(company)
        for field in ("blockStatus", "gptBlockResult"):
            values = payload.get(field) or []
            if values:
                allowed = {str(value).lower() for value in values}
                rows = [
                    row for row in rows if str(row.get(field) or "").lower() in allowed
                ]
        start = (page_num - 1) * page_size
        return linkage_success(
            {"total": len(rows), "data": rows[start : start + page_size]}
        )

    def _entity_objects(self, company: dict[str, Any]) -> list[dict[str, Any]]:
        entities = company.get("entities") or {}
        explicit = entities.get("objects")
        if explicit:
            return [copy.deepcopy(item) for item in explicit]
        mode = str(entities.get("mode") or "security").lower()
        stats = entities.get("stats") or {}
        total = int(stats.get("total") or 0)
        banned = int(stats.get("banned") or 0)
        allowed = int(stats.get("allowed") or 0)
        base_ts = parse_iso(f"{self._report_date}T12:00:00+08:00") or self._fixed_now()
        rows = []
        for index in range(total):
            if index < banned:
                gpt = "blocked"
                block = "blocked" if mode == "security" else "unblocked"
            elif index < banned + allowed:
                gpt = "unblocked"
                block = "unblocked"
            else:
                gpt = "observing" if index % 2 else "observed"
                block = "unblocked"
            is_ip = index % 3 != 2
            object_name = (
                f"203.0.113.{index + 1}" if is_ip else f"malware-{index}.example.com"
            )
            rows.append(
                {
                    "id": f"entity-{company['company_id']}-{index:04d}",
                    "threatObject": {
                        "threatObjectName": object_name,
                        "country": "美国" if is_ip else "",
                        "province": "",
                        "city": "",
                    },
                    "threatType": "IPv4" if is_ip else "Domain",
                    "threatLevel": ("high", "medium", "low")[index % 3],
                    "gptBlockResult": gpt,
                    "blockStatus": block,
                    "blockHistory": {"blockCount": max(0, (total - index) % 7)},
                    "firstTimestamp": base_ts - (index + 1) * 86_400_000,
                    "lastTimestamp": base_ts - index * 60_000,
                    "updateTimestamp": base_ts - index * 60_000,
                    "companyId": company["company_id"],
                }
            )
        return rows

    # ------------------------------------------------------------------ #
    # XDR (through the MSSP api-proxy)
    # ------------------------------------------------------------------ #

    def _xdr_alert_count(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        start_sec, _end_sec = self._xdr_window(payload)
        return MsspResponse(200, {"data": {"total": self._alert_count_for_epochs(company, start_sec)}})

    def _xdr_alert_table(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> MsspResponse:
        start_sec, end_sec = self._xdr_window(payload)
        date = format_date_from_epoch(start_sec)
        profile = self._alert_profile(company, date)
        total = int(profile.get("count") or 0)
        table = payload.get("table") or {}
        page_num = max(1, int(table.get("pageNum") or 1))
        page_size = max(1, int(table.get("pageSize") or 100))
        rows = self._alert_rows(company, date, start_sec, end_sec, profile)
        start = (page_num - 1) * page_size
        return MsspResponse(
            200, {"data": {"total": total, "data": rows[start : start + page_size]}}
        )

    def _xdr_window(self, payload: dict[str, Any]) -> tuple[int, int]:
        global_condition = payload.get("globalCondition") or {}
        time_window = global_condition.get("time") or {}
        begin = (time_window.get("begin") or {}).get("value")
        end = (time_window.get("end") or {}).get("value")
        if begin is None:
            start = parse_iso(f"{self._report_date}T00:00:00+08:00")
            end_value = parse_iso(f"{self._report_date}T23:59:59+08:00")
            return int(start), int(end_value)
        return int(begin), int(end)

    def _alert_count_for_window(
        self, payload: dict[str, Any], company: dict[str, Any]
    ) -> int:
        global_condition = payload.get("globalCondition") or {}
        time_window = global_condition.get("time") or {}
        begin = (time_window.get("begin") or {}).get("value")
        if begin is None:
            begin = payload.get("fromDate")
        if isinstance(begin, str):
            text = begin.strip().replace(" ", "T")
            if "+" not in text[10:] and not text.endswith("Z"):
                text += "+08:00"
            begin = parse_iso(text)
        if begin is None:
            return 0
        return self._alert_count_for_epochs(company, int(begin))

    def _alert_count_for_epochs(self, company: dict[str, Any], start_sec: int) -> int:
        date = format_date_from_epoch(start_sec)
        return int(self._alert_profile(company, date).get("count") or 0)

    def _alert_profile(self, company: dict[str, Any], date: str) -> dict[str, Any]:
        alerts = company.get("alerts") or {}
        days = alerts.get("days") or {}
        profile = days.get(date)
        if profile is None:
            profile = alerts.get("default") or {"count": 0}
        return profile if isinstance(profile, dict) else {"count": 0}

    def _alert_ips(self, company: dict[str, Any]) -> list[str]:
        ips: list[str] = []
        alerts = company.get("alerts") or {}
        for profile in (alerts.get("days") or {}).values():
            if not isinstance(profile, dict):
                continue
            for ip in profile.get("host_ips") or []:
                text = str(ip).strip()
                if text and text not in ips:
                    ips.append(text)
        return ips

    def _alert_rows(
        self,
        company: dict[str, Any],
        date: str,
        start_sec: int,
        end_sec: int,
        profile: dict[str, Any],
    ) -> list[dict[str, Any]]:
        total = int(profile.get("count") or 0)
        if total <= 0:
            return []
        types = self._expand_weights(profile.get("types"), total)
        sources = [str(item) for item in profile.get("sources") or ["203.0.113.10"]]
        host_ips = [str(item) for item in profile.get("host_ips") or ["10.10.1.11"]]
        dev_sources = [str(item) for item in profile.get("dev_sources") or []]
        span = max(1, end_sec - start_sec)
        rows = []
        for index in range(total):
            type_name = types[index]
            source = sources[index % len(sources)]
            host_ip = host_ips[index % len(host_ips)]
            last_time = start_sec + span * index // max(total, 1)
            row: dict[str, Any] = {
                "uuId": _scalar(f"{company['company_id']}-{date}-{index:04d}"),
                "lastTime": _scalar(last_time, format_epoch(last_time)),
                "name": _scalar(f"{type_name}告警"),
                "severity": _scalar(RISK_CODES["high"], "高危"),
                "srcIp": _array([source]),
                "dstIp": _array([host_ip]),
                "hostIp": _array([host_ip]),
                "attackResult": _scalar("失败"),
                "dealStatus": _scalar("未处置"),
                "dealAction": _scalar("待处置"),
                "whiteStatus": _scalar("未加白"),
                "devSourceNames": _array(dev_sources[index % len(dev_sources)] if dev_sources else ""),
                "threatClass": _scalar("网络攻击", "网络攻击"),
                "threatType": _scalar(f"9{index % 5}", "网络攻击"),
                "threatSubType": _scalar(f"9{index % 5}_{index % 4}_{index % 3}", type_name),
                "threatTypeProxy": _scalar(f"9{index % 5}", "网络攻击"),
                "threatSubTypeProxy": _scalar(f"9{index % 5}_{index % 4}_{index % 3}", type_name),
                "hostCountryName": _scalar("中国"),
                "hostProvinceName": _scalar("江苏省"),
                "gptResult": _scalar(130, "真实攻击未成功"),
                "srcIpInfos": _array(
                    [
                        {
                            "ip": source,
                            "countryName": GEO_BY_SOURCE.get(source, {}).get("countryName", "美国"),
                            "provinceName": GEO_BY_SOURCE.get(source, {}).get("provinceName", ""),
                            "cityName": GEO_BY_SOURCE.get(source, {}).get("cityName", ""),
                        }
                    ]
                ),
            }
            rows.append(row)
        return rows

    def _expand_weights(self, weights: Any, total: int) -> list[str]:
        if not isinstance(weights, dict) or not weights:
            return ["Web攻击"] * total
        items = [(str(name), max(0, int(weight))) for name, weight in weights.items()]
        weight_sum = sum(weight for _, weight in items)
        if weight_sum <= 0:
            return [items[0][0]] * total
        out: list[str] = []
        remaining = total
        for index, (name, weight) in enumerate(items):
            if index == len(items) - 1:
                count = remaining
            else:
                count = round(total * weight / weight_sum)
                count = min(count, remaining)
            out.extend([name] * count)
            remaining -= count
        while len(out) < total:
            out.append(items[-1][0])
        return out

    # ------------------------------------------------------------------ #
    # WeCom (write operations are off by default)
    # ------------------------------------------------------------------ #

    def _wecom_disabled(self) -> MsspResponse:
        return business_error(WECOM_DISABLED_CODE, "wecom 写操作未开启", status=403)

    def _wecom_query(self, company: dict[str, Any]) -> MsspResponse:
        if not self.wecom_write:
            return self._wecom_disabled()
        group = company.get("group") or {
            "_id": f"group-{company['company_id']}",
            "group_name": f"{company.get('name')}服务群",
            "company_name": company.get("name"),
        }
        return success(
            {
                "total": 1,
                "list": [
                    {
                        "actual_data": {
                            "actual_data_value": {
                                "_id": str(group.get("_id") or ""),
                                "group_name": str(group.get("group_name") or ""),
                                "company_name": str(group.get("company_name") or ""),
                            }
                        }
                    }
                ],
            }
        )

    def _wecom_upload(
        self, headers: Mapping[str, str], body: bytes
    ) -> MsspResponse:
        if not self.wecom_write:
            return self._wecom_disabled()
        content_type = str(headers.get("content-type") or "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise MsspBadRequest(CODE_PARAMS_ERR, "附件上传必须使用 multipart/form-data")
        file_name, file_bytes = _read_multipart_file(headers, body)
        if not file_name:
            raise MsspBadRequest(CODE_PARAMS_ERR, "附件上传缺少文件")
        digest = hashlib.sha256(file_bytes).hexdigest()
        upload_id = f"file-{digest[:10]}"
        if upload_id not in self.uploads:
            self.uploads[upload_id] = {
                "_id": upload_id,
                "upload_id": upload_id,
                "file_id": upload_id,
                "file_name": file_name,
                "size": len(file_bytes),
                "sha256": digest,
            }
        info = self.uploads[upload_id]
        return success({"file": dict(info)}, msg="success")

    def _wecom_send(
        self, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> MsspResponse:
        if not self.wecom_write:
            return self._wecom_disabled()
        msg = str(payload.get("msg") or "")
        if not msg.strip():
            raise MsspBadRequest(CODE_PARAMS_ERR, "msg 不能为空")
        company = payload.get("company_name") or {}
        group = payload.get("wechat_group_name") or {}
        company_id = str(company.get("_id") or "")
        group_id = str(group.get("_id") or "")
        attachments = [str(item) for item in payload.get("attachments") or []]
        trace_id = str(headers.get("traceid") or "").strip()
        fingerprint = trace_id or _json_fingerprint(
            {
                "company_id": company_id,
                "group_id": group_id,
                "msg": msg,
                "attachments": attachments,
            }
        )
        if fingerprint in self.sent:
            self.duplicate_sends += 1
            return success(msg="duplicate")
        self.sent[fingerprint] = {
            "fingerprint": fingerprint,
            "trace_id": trace_id,
            "company_id": company_id,
            "group_id": group_id,
            "msg_sha256": hashlib.sha256(msg.encode("utf-8")).hexdigest(),
            "attachments": attachments,
        }
        return success(msg="success")

    # ------------------------------------------------------------------ #
    # Faults / stats control plane
    # ------------------------------------------------------------------ #

    def _fault_for(self, key: str, path: str, company_id: str) -> MsspResponse | None:
        for fault in list(self.faults):
            target = str(fault.get("path") or "")
            if target not in (key, path):
                continue
            if fault.get("company_id") and str(fault["company_id"]) != company_id:
                continue
            delay_ms = int(fault.get("delay_ms") or 0)
            if delay_ms > 0:
                time.sleep(min(delay_ms, 10_000) / 1000)
            times = int(fault.get("times", 1))
            if times > 0:
                fault["times"] = times - 1
                if fault["times"] <= 0:
                    self.faults.remove(fault)
            if not fault.get("http_status") and not fault.get("body"):
                return None
            self.faults_served += 1
            status = int(fault.get("http_status") or 200)
            body = fault.get("body")
            if isinstance(body, dict):
                return MsspResponse(status, body)
            code = int(fault.get("business_code") or (CODE_AUTH_FAILED if status == 401 else CODE_INTERNAL))
            message = str(fault.get("msg") or ("登录态失效" if status == 401 else "服务内部错误"))
            return MsspResponse(status, {"code": code, "msg": message})
        return None

    def _handle_control(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes
    ) -> MsspResponse:
        del headers
        if method == "GET" and path == "/__stats":
            return MsspResponse(200, self.stats())
        if method == "GET" and path == "/__fixtures":
            return MsspResponse(200, self.fixture_summary())
        if method == "POST" and path == "/__control/faults":
            payload = self._json_body(body)
            if not payload.get("path"):
                raise MsspBadRequest(CODE_PARAMS_ERR, "fault 需要 path")
            self.faults.append(dict(payload))
            return MsspResponse(200, {"ok": True, "faults": self.faults})
        if method == "POST" and path == "/__control/wecom":
            payload = self._json_body(body)
            self.wecom_write = bool(payload.get("enabled"))
            return MsspResponse(200, {"ok": True, "wecomWrite": self.wecom_write})
        if method == "POST" and path == "/__control/reset":
            self.request_counts.clear()
            self.faults.clear()
            self.faults_served = 0
            self.uploads.clear()
            self.sent.clear()
            self.duplicate_sends = 0
            return MsspResponse(200, {"ok": True})
        return MsspResponse(404, {"code": 4004, "msg": "not found"})

    def stats(self) -> dict[str, Any]:
        return {
            "counts": dict(self.request_counts),
            "writes": {
                "uploads": len(self.uploads),
                "sends": len(self.sent),
                "duplicates": self.duplicate_sends,
            },
            "sent": list(self.sent.values()),
            "faults": list(self.faults),
            "faultsServed": self.faults_served,
            "wecomWrite": self.wecom_write,
        }

    def fixture_summary(self) -> dict[str, Any]:
        companies = []
        for cid, fixture in (self.fixtures.get("companies") or {}).items():
            companies.append(
                {
                    "company_id": str(cid),
                    "name": fixture.get("name") or cid,
                    "full_asset": bool(fixture.get("full_asset")),
                    "entities_mode": (fixture.get("entities") or {}).get("mode", "security"),
                }
            )
        return {"report_date": self._report_date, "companies": companies}

    def _fixed_now(self) -> int:
        return parse_iso(f"{self._report_date}T12:00:00+08:00") or int(time.time())


def _scalar(value: Any, rendered: Any | None = None) -> dict[str, Any]:
    return {"originalValue": value, "renderValue": value if rendered is None else rendered}


def _array(values: Any) -> dict[str, Any]:
    if not isinstance(values, list):
        values = [values] if values not in (None, "") else []
    items = [
        {"originalValue": value, "renderValue": value}
        for value in values
        if value not in (None, "")
    ]
    return {"data": items, "count": len(items)}


def _manage_type_matches(row: dict[str, Any], spec: Any) -> bool:
    if not isinstance(spec, list) or not spec:
        return False
    for item in spec:
        if isinstance(item, str):
            if row["manage_type"] == item:
                return True
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            if row["manage_type"] == item[0] and row["manage_sub_type"] == item[1]:
                return True
    return False


def _json_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_multipart_file(
    headers: Mapping[str, str], body: bytes
) -> tuple[str, bytes]:
    content_type = str(headers.get("content-type") or "")
    synthetic = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + body
    )
    message = BytesParser(policy=email_policy).parsebytes(synthetic)
    if not message.is_multipart():
        return "", b""
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") != "file":
            continue
        file_name = part.get_filename() or "attachment.bin"
        payload = part.get_payload(decode=True) or b""
        return str(file_name), payload
    return "", b""
