"""TASK-020: real HTTP contract for the fake MSSP platform mode."""

from __future__ import annotations

import httpx
import pytest

from tests.tools import mssp_support as support
from tests.tools.mssp_support import (
    PATH_ASSET_QUERY,
    PATH_BRANCH_DEV,
    PATH_CK_COUNT,
    PATH_CK_QUERY_LIST,
    PATH_CK_TABLE_QUERY,
    PATH_COMPANY_LIST,
    PATH_EVENT_DETAIL,
    PATH_EVENT_GRADING_TYPE,
    PATH_EVENT_REMARK,
    PATH_EVENT_TABLE,
    PATH_MANAGE_TYPE,
    PATH_SERVICE_INFO,
    PATH_THREAT_OBJECTS,
    PATH_THREAT_RESPONSE_MODE,
    PATH_THREAT_STATISTICS,
    PATH_WHITELIST,
    PATH_XDR_ALERT_COUNT,
    PATH_XDR_ALERT_TABLE,
    PREVIOUS_DATE,
    REPORT_DATE,
    day_window,
    payload,
    running_mssp,
    xdr_count_body,
    xdr_query_body,
)

pytestmark = pytest.mark.integration


def event_body(
    *,
    offset: int = 0,
    limit: int = 100,
    event_status: list[str] | None = None,
    service_status: list[int] | None = None,
    event_name: str = "",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    start, end = day_window(REPORT_DATE)
    body: dict[str, object] = {
        "order": {},
        "offset": offset,
        "limit": limit,
        "event_grading_tags": [],
        "event_status": event_status or [],
        "service_status": service_status or [],
        "event_name": event_name,
        "create_time": [start * 1000, end * 1000],
        "company_id": ["1001"],
    }
    body.update(extra or {})
    return body


def test_ready_advertises_host_csrf_and_fixture_catalog() -> None:
    with running_mssp() as platform:
        ready = platform.ready
        assert ready["status"] == "ready"
        assert ready["platform"] == "mssp"
        assert ready["hostHeader"] == support.MSSP_HOST
        assert ready["csrfHeader"] == "X-CSRFToken"
        assert ready["csrfToken"] == support.MSSP_CSRF
        assert ready["companyHeader"] == "X-MSSW-Company-Id"
        assert ready["wecomWrite"] is False
        assert ready["endpoints"]["company_list"] == PATH_COMPANY_LIST
        assert ready["endpoints"]["xdr_alert_table"] == PATH_XDR_ALERT_TABLE
        companies = {row["company_id"] for row in ready["companies"]}
        assert {"1001", "1002", "1003", "1004"} <= companies
        assert {f"21{index:02d}" for index in range(1, 12)} <= companies


def test_host_csrf_and_api_proxy_company_header_are_enforced() -> None:
    with running_mssp() as platform:
        # Default httpx Host is 127.0.0.1:<port>; the fake routes by Host.
        wrong_host = httpx.post(
            platform.base_url + PATH_CK_TABLE_QUERY,
            json={"tableType": True},
            headers=platform.headers(host="evil.example.com"),
            timeout=5,
        )
        assert wrong_host.status_code == 404
        assert wrong_host.json()["code"] == 4004

        missing_csrf = platform.headers()
        missing_csrf.pop("X-CSRFToken")
        rejected = platform.post(PATH_CK_TABLE_QUERY, {"tableType": True}, headers=missing_csrf)
        assert rejected.status_code == 403
        assert rejected.json() == {"code": 9451, "msg": "非法操作"}

        wrong_csrf = platform.post(
            PATH_CK_TABLE_QUERY,
            {"tableType": True},
            headers=platform.headers(csrf="not-the-platform-token"),
        )
        assert wrong_csrf.status_code == 403
        assert wrong_csrf.json()["code"] == 9451

        no_company = platform.headers(include_company=False)
        response = platform.post(
            PATH_THREAT_OBJECTS,
            {"pageNum": 1, "pageSize": 10},
            headers=no_company,
        )
        assert response.status_code == 400
        assert response.json() == {"code": 1003, "msg": "apiproxy缺失必要路由头"}

        # GET getresponsemode does not require CSRF (the real gateway checks POST only).
        response = platform.get(
            PATH_THREAT_RESPONSE_MODE,
            headers={"Host": support.MSSP_HOST, "X-MSSW-Company-Id": "1001"},
        )
        assert response.status_code == 200
        assert response.json()["code"] == "Success"


def test_company_list_and_service_info_shape() -> None:
    with running_mssp() as platform:
        companies = payload(
            platform.post(PATH_COMPANY_LIST, {"keyword": "1001", "offset": 0, "limit": 100})
        )
        assert companies["code"] == 0
        assert companies["data"]["total"] == 1
        assert companies["data"]["list"][0]["company_id"] == "1001"

        full = payload(
            platform.post(
                PATH_SERVICE_INFO,
                {"offset": 0, "limit": 100, "company_id": "1001"},
            )
        )
        full_codes = {row["main_service_code"] for row in full["data"]["list"]}
        assert full_codes & {"S-01-01-020", "S-01-01-022", "S-01-01-023"}

        non_full = payload(
            platform.post(
                PATH_SERVICE_INFO,
                {"offset": 0, "limit": 100, "company_id": "1002"},
            )
        )
        non_full_codes = {row["main_service_code"] for row in non_full["data"]["list"]}
        assert non_full_codes.isdisjoint({"S-01-01-020", "S-01-01-022", "S-01-01-023"})


def test_data_lake_tables_counts_and_query_list_contract() -> None:
    with running_mssp() as platform:
        tables = payload(platform.post(PATH_CK_TABLE_QUERY, {"tableType": True}))
        names = {row["tableName"]: row["id"] for row in tables["data"]["list"]}
        assert names["网络安全日志"] == 6408
        assert names["终端安全日志"] == 6409
        assert names["XDR告警表"] == 6410

        start, end = day_window(REPORT_DATE)
        body = {
            "fromDate": "2026-11-18 00:00:00",
            "toDate": "2026-11-18 23:59:59",
            "tableId": [6408, 6409],
            "constrains": [],
        }
        counts = payload(platform.post(PATH_CK_COUNT, body))
        assert counts["data"]["total"] == 2000
        alert_counts = payload(platform.post(PATH_CK_COUNT, {**body, "tableId": [6410]}))
        assert alert_counts["data"]["total"] == 24

        valid = payload(
            platform.post(
                PATH_CK_QUERY_LIST,
                {"pageNo": 0, "pageSize": 100, "fieldList": ["log_time", "src_ip"]},
            )
        )
        assert valid["data"]["condiResults"]
        assert {"fieldName": "log_time", "fieldAlias": "时间"} in valid["data"]["tableFields"]

        missing = payload(platform.post(PATH_CK_QUERY_LIST, {"pageNo": 0, "pageSize": 100}))
        assert missing["code"] == 9001
        unsupported = payload(
            platform.post(
                PATH_CK_QUERY_LIST,
                {"pageNo": 0, "pageSize": 100, "fieldList": [], "isAgg": True},
            )
        )
        assert unsupported["code"] == 9001


def test_event_table_requires_wire_fields_and_filters() -> None:
    with running_mssp() as platform:
        missing_order = platform.post(PATH_EVENT_TABLE, {"offset": 0, "limit": 10})
        assert payload(missing_order)["code"] == 9064
        bad_limit = platform.post(PATH_EVENT_TABLE, event_body(limit=50))
        assert payload(bad_limit)["code"] == 9064
        bad_offset = platform.post(PATH_EVENT_TABLE, event_body(offset=5, limit=10))
        assert payload(bad_offset)["code"] == 9064

        finished = payload(
            platform.post(PATH_EVENT_TABLE, event_body(event_status=["finished"]))
        )
        assert finished["data"]["total"] == 4
        assert {row["event_status"] for row in finished["data"]["list"]} == {"finished"}

        service_zero = payload(
            platform.post(
                PATH_EVENT_TABLE,
                event_body(event_status=["finished"], service_status=[0]),
            )
        )
        assert service_zero["data"]["total"] == 3
        assert {row["service_status"] for row in service_zero["data"]["list"]} == {0}

        af = payload(
            platform.post(
                PATH_EVENT_TABLE,
                event_body(event_name="AF存在直通策略", event_status=["finished"]),
            )
        )
        assert af["data"]["total"] == 1
        assert af["data"]["list"][0]["event_name"] == "AF存在直通策略"

        page = payload(platform.post(PATH_EVENT_TABLE, event_body(limit=10, offset=0)))
        assert page["data"]["total"] == 6
        assert len(page["data"]["list"]) <= 10
        assert all("event_id" in row for row in page["data"]["list"])

        offline = payload(
            platform.post(
                PATH_EVENT_TABLE,
                event_body(
                    event_status=["finished"],
                    extra={"manage_type": [["STRATEGY_OPTIMIZE", "DEVICE_OFFLINE"]]},
                ),
            )
        )
        assert offline["data"]["total"] == 0


def test_event_remark_detail_and_facet_endpoints() -> None:
    with running_mssp() as platform:
        all_events = payload(platform.post(PATH_EVENT_TABLE, event_body()))

        accept_risk = next(
            row for row in all_events["data"]["list"] if row["event_status"] == "accept_risk"
        )
        remark = payload(
            platform.post(
                PATH_EVENT_REMARK,
                {"order": "desc", "offset": 0, "limit": 10, "event_id": accept_risk["event_id"]},
            )
        )
        assert remark["data"]["total"] == 1
        assert "技术误报" in remark["data"]["list"][0]["remark_txt"]
        assert remark["data"]["list"][0]["event_status"] == "accept_risk"

        detail = payload(
            platform.post(PATH_EVENT_DETAIL, {"event_id": accept_risk["event_id"]})
        )
        assert detail["data"]["base_info"]["event_id"] == accept_risk["event_id"]
        assert detail["data"]["base_info"]["event_name"] == accept_risk["event_name"]

        grading = payload(platform.post(PATH_EVENT_GRADING_TYPE, {}))
        assert grading["data"]["total"] == 6
        assert {row["event_grading_type"] for row in grading["data"]["list"]} == {
            "high",
            "medium",
            "low",
        }

        manage = payload(platform.post(PATH_MANAGE_TYPE, {}))
        manage_rows = {row["manage_type"]: row["count"] for row in manage["data"]["list"]}
        assert manage_rows["INTERNET_THREAT"] == 3
        assert manage_rows["STRATEGY_OPTIMIZE"] == 1

        rejected = platform.post(PATH_MANAGE_TYPE, {"manage_type": ["INTERNET_THREAT"]})
        assert payload(rejected)["code"] == 9064
        rejected = platform.post(PATH_EVENT_GRADING_TYPE, {"event_grading_tags": []})
        assert payload(rejected)["code"] == 9064


def test_asset_device_and_whitelist_endpoints() -> None:
    with running_mssp() as platform:
        start, end = day_window(REPORT_DATE)
        new_assets = payload(
            platform.post(
                PATH_ASSET_QUERY,
                {
                    "order": "asc",
                    "offset": 0,
                    "limit": 100,
                    "first_time": [start * 1000, end * 1000],
                    "authorization_type": [1, 2, 3],
                    "service_status": [],
                    "company_id": "2101",
                },
                company_id="2101",
            )
        )
        assert new_assets["data"]["total"] == 1
        asset = new_assets["data"]["list"][0]
        assert asset["asset"] == "10.20.1.10"
        assert asset["authorization_type"] == [1]
        assert isinstance(asset["first_time"], int)

        by_ip = payload(
            platform.post(
                PATH_ASSET_QUERY,
                {
                    "order": "asc",
                    "offset": 0,
                    "limit": 10,
                    "ip_url_keyword": "10.20.1.21",
                    "first_time": [],
                    "company_id": "1002",
                },
                company_id="1002",
            )
        )
        assert by_ip["data"]["total"] == 1
        assert by_ip["data"]["list"][0]["asset"] == "10.20.1.21"

        devices = payload(
            platform.post(
                PATH_BRANCH_DEV,
                {"type": 3, "status": 1, "offset": 0, "limit": 100, "company_id": "2102"},
                company_id="2102",
            )
        )
        assert devices["data"]["total"] == 1
        device = devices["data"]["list"][0]
        assert device["gateway_id"] == "AF210200"
        assert device["dev_status"] == 1
        assert isinstance(device["create_time"], int)

        whitelist = payload(
            platform.post(
                PATH_WHITELIST,
                {
                    "order": {"update_time": "desc"},
                    "offset": 0,
                    "limit": 100,
                    "valid_time": [start * 1000, end * 1000],
                    "company_id": "2108",
                },
                company_id="2108",
            )
        )
        assert whitelist["data"]["total"] == 1
        assert whitelist["data"]["list"][0]["online"] == 1
        assert whitelist["data"]["list"][0]["valid_time"] == REPORT_DATE


def test_threat_entity_modes_stats_and_object_filters() -> None:
    with running_mssp() as platform:
        mode = payload(platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1001"))
        assert mode == {"code": "Success", "data": {"responseMode": "security"}}

        stats = payload(
            platform.post(
                PATH_THREAT_STATISTICS,
                {
                    "startTime": day_window(REPORT_DATE)[0] * 1000,
                    "endTime": day_window(REPORT_DATE)[1] * 1000,
                },
            )
        )
        assert stats["data"] == {
            "totalCount": 12,
            "blockedCount": 6,
            "unblockedCount": 4,
            "observedCount": 2,
        }

        blocked = payload(
            platform.post(
                PATH_THREAT_OBJECTS,
                {"blockStatus": ["blocked"], "pageNum": 1, "pageSize": 100},
            )
        )
        assert blocked["data"]["total"] == 6
        assert all(row["blockStatus"] == "blocked" for row in blocked["data"]["data"])
        first = blocked["data"]["data"][0]
        assert first["threatObject"]["threatObjectName"]
        assert "blockCount" in first["blockHistory"]

        page = payload(
            platform.post(
                PATH_THREAT_OBJECTS,
                {"blockStatus": ["blocked"], "pageNum": 2, "pageSize": 3},
            )
        )
        assert page["data"]["total"] == 6
        assert len(page["data"]["data"]) == 3
        assert page["data"]["data"][0]["id"] != first["id"]

        too_large = payload(
            platform.post(PATH_THREAT_OBJECTS, {"pageNum": 1, "pageSize": 200})
        )
        assert too_large["code"] == "InternalError.InvalidParameter"

        mock_mode = payload(platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1002"))
        assert mock_mode["data"]["responseMode"] == "mock"
        recommended = payload(
            platform.post(
                PATH_THREAT_OBJECTS,
                {"gptBlockResult": ["blocked"], "pageNum": 1, "pageSize": 100},
                company_id="1002",
            )
        )
        assert recommended["data"]["total"] == 9
        assert all(row["gptBlockResult"] == "blocked" for row in recommended["data"]["data"])
        assert all(row["blockStatus"] == "unblocked" for row in recommended["data"]["data"])

        disabled = platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1004")
        assert disabled.status_code == 200
        assert disabled.json()["code"] == "AuthFailure.AutoDisposeAuthError"


def test_xdr_alert_table_and_count_through_api_proxy() -> None:
    with running_mssp() as platform:
        count = payload(platform.post(PATH_XDR_ALERT_COUNT, xdr_count_body(REPORT_DATE)))
        assert count["data"]["total"] == 24

        table = payload(
            platform.post(
                PATH_XDR_ALERT_TABLE,
                xdr_query_body(REPORT_DATE, page_size=5),
            )
        )
        assert table["data"]["total"] == 24
        assert len(table["data"]["data"]) == 5
        row = table["data"]["data"][0]
        assert row["lastTime"]["renderValue"].startswith(REPORT_DATE)
        assert row["hostIp"]["data"][0]["renderValue"] == "10.10.1.11"
        assert row["srcIp"]["data"][0]["renderValue"] == "203.0.113.10"
        assert row["threatSubTypeProxy"]["renderValue"] == "Web攻击"
        assert row["srcIpInfos"]["data"][0]["renderValue"]["countryName"] == "美国"

        missing = payload(
            platform.post(
                PATH_XDR_ALERT_TABLE,
                xdr_query_body(PREVIOUS_DATE),
            )
        )
        assert missing["data"]["total"] == 12

        empty = payload(
            platform.post(PATH_XDR_ALERT_COUNT, xdr_count_body(REPORT_DATE), company_id="1003")
        )
        assert empty["data"]["total"] == 0


def test_stats_and_reset_are_observable() -> None:
    with running_mssp() as platform:
        platform.reset()
        platform.post(PATH_COMPANY_LIST, {"keyword": "", "offset": 0, "limit": 10})
        platform.post(PATH_EVENT_TABLE, event_body(event_status=["finished"]))
        stats = platform.stats()
        assert stats["counts"][PATH_COMPANY_LIST] == 1
        assert stats["counts"][PATH_EVENT_TABLE] == 1
        assert stats["writes"] == {"uploads": 0, "sends": 0, "duplicates": 0}
        platform.reset()
        assert platform.stats()["counts"] == {}


def test_unknown_business_path_is_404() -> None:
    with running_mssp() as platform:
        response = platform.post("/gateway/does-not-exist", {})
        assert response.status_code == 404
        assert response.json()["code"] == 4004


def test_fixture_catalog_endpoint() -> None:
    with running_mssp() as platform:
        response = httpx.get(platform.base_url + "/__fixtures", timeout=5)
        assert response.status_code == 200
        body = response.json()
        assert body["report_date"] == REPORT_DATE
        by_id = {row["company_id"]: row for row in body["companies"]}
        assert by_id["1001"]["full_asset"] is True
        assert by_id["1002"]["full_asset"] is False
        assert by_id["1002"]["entities_mode"] == "mock"
        assert by_id["1004"]["entities_mode"] == "disabled"


def test_json_body_is_not_reflected_in_logs() -> None:
    with running_mssp("--verbose") as platform:
        platform.post(
            PATH_CK_TABLE_QUERY,
            {"tableType": True, "extra": "should-not-be-logged"},
        )
    assert "should-not-be-logged" not in platform.logs
