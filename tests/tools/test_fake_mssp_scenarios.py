"""TASK-021: deterministic MSSP fixtures, scene matrix and failure injection."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.tools import mssp_support as support
from tests.tools.mssp_support import (
    PATH_ASSET_QUERY,
    PATH_ATTACHMENT_UPLOAD,
    PATH_BRANCH_DEV,
    PATH_CK_COUNT,
    PATH_COMPANY_LIST,
    PATH_EVENT_TABLE,
    PATH_SERVICE_INFO,
    PATH_THREAT_OBJECTS,
    PATH_THREAT_RESPONSE_MODE,
    PATH_THREAT_STATISTICS,
    PATH_WECOM_MESSAGE,
    PATH_WECOM_QUERY,
    PATH_WHITELIST,
    PATH_XDR_ALERT_COUNT,
    PATH_XDR_ALERT_TABLE,
    PREVIOUS_DATE,
    REPORT_DATE,
    day_window,
    event_query_body,
    payload,
    running_mssp,
    xdr_count_body,
    xdr_query_body,
)

pytestmark = pytest.mark.integration


def counters(platform: support.FakeMssp, company_id: str) -> tuple[int, int]:
    current = payload(
        platform.post(PATH_XDR_ALERT_COUNT, xdr_count_body(REPORT_DATE), company_id=company_id)
    )
    previous = payload(
        platform.post(PATH_XDR_ALERT_COUNT, xdr_count_body(PREVIOUS_DATE), company_id=company_id)
    )
    return current["data"]["total"], previous["data"]["total"]


def alerts(platform: support.FakeMssp, company_id: str, date: str) -> list[dict[str, object]]:
    data = payload(
        platform.post(
            PATH_XDR_ALERT_TABLE,
            xdr_query_body(date, page_size=1000),
            company_id=company_id,
        )
    )
    return list(data["data"]["data"])


def alert_sources(rows: list[dict[str, object]]) -> set[str]:
    values = set()
    for row in rows:
        for item in (row.get("srcIp") or {}).get("data") or []:
            values.add(str(item.get("renderValue")))
    return values


def alert_types(rows: list[dict[str, object]]) -> set[str]:
    return {str((row.get("threatSubTypeProxy") or {}).get("renderValue")) for row in rows}


def test_full_asset_fixture_has_counts_events_and_security_entities() -> None:
    with running_mssp() as platform:
        services = payload(
            platform.post(
                PATH_SERVICE_INFO,
                {"offset": 0, "limit": 100, "company_id": "1001"},
            )
        )
        assert any(
            row["main_service_code"] in {"S-01-01-020", "S-01-01-022", "S-01-01-023"}
            and str(row["auth_status"]) == "1"
            and row["service_status"] == "running"
            for row in services["data"]["list"]
        )

        logs = payload(
            platform.post(
                PATH_CK_COUNT,
                {
                    "fromDate": "2026-11-18 00:00:00",
                    "toDate": "2026-11-18 23:59:59",
                    "tableId": [6408, 6409],
                    "constrains": [],
                },
            )
        )
        assert logs["data"]["total"] == 2000
        assert counters(platform, "1001") == (24, 12)

        events = payload(
            platform.post(PATH_EVENT_TABLE, event_query_body("1001", REPORT_DATE))
        )
        assert events["data"]["total"] == 6
        assert any(
            row["event_status"] == "accept_risk" for row in events["data"]["list"]
        )
        assert any(
            row["event_name"] == "AF存在直通策略" for row in events["data"]["list"]
        )

        mode = payload(platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1001"))
        assert mode["data"]["responseMode"] == "security"
        stats = payload(
            platform.post(
                PATH_THREAT_STATISTICS,
                {
                    "startTime": day_window(REPORT_DATE)[0] * 1000,
                    "endTime": day_window(REPORT_DATE)[1] * 1000,
                },
            )
        )
        assert stats["data"]["totalCount"] == 12
        assert stats["data"]["blockedCount"] == 6


def test_non_full_asset_fixture_has_inited_58_and_mock_entities() -> None:
    with running_mssp() as platform:
        services = payload(
            platform.post(
                PATH_SERVICE_INFO,
                {"offset": 0, "limit": 100, "company_id": "1002"},
            )
        )
        codes = {row["main_service_code"] for row in services["data"]["list"]}
        assert codes.isdisjoint({"S-01-01-020", "S-01-01-022", "S-01-01-023"})

        settled = payload(
            platform.post(
                PATH_EVENT_TABLE,
                event_query_body(
                    "1002",
                    REPORT_DATE,
                    event_status=["disposal", "finished", "accept_risk"],
                    service_status=[0, 1, 3],
                ),
            )
        )
        assert settled["data"]["total"] == 6
        inited = payload(
            platform.post(
                PATH_EVENT_TABLE,
                event_query_body(
                    "1002",
                    REPORT_DATE,
                    event_status=["inited"],
                    service_status=[3],
                ),
            )
        )
        assert inited["data"]["total"] == 1
        assert inited["data"]["list"][0]["service_status"] == 3

        mode = payload(platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1002"))
        assert mode["data"]["responseMode"] == "mock"
        recommended = payload(
            platform.post(
                PATH_THREAT_OBJECTS,
                {"gptBlockResult": ["blocked"], "pageNum": 1, "pageSize": 100},
                company_id="1002",
            )
        )
        assert recommended["data"]["total"] == 9
        assert counters(platform, "1002") == (8, 8)


def test_empty_fixture_returns_zero_without_errors() -> None:
    with running_mssp() as platform:
        logs = payload(
            platform.post(
                PATH_CK_COUNT,
                {
                    "fromDate": "2026-11-18 00:00:00",
                    "toDate": "2026-11-18 23:59:59",
                    "tableId": [6408, 6409, 6410],
                    "constrains": [],
                },
                company_id="1003",
            )
        )
        assert logs["data"]["total"] == 0
        assert counters(platform, "1003") == (0, 0)
        events = payload(
            platform.post(PATH_EVENT_TABLE, event_query_body("1003", REPORT_DATE))
        )
        assert events["data"] == {"total": 0, "list": []}
        stats = payload(
            platform.post(
                PATH_THREAT_STATISTICS,
                {
                    "startTime": day_window(REPORT_DATE)[0] * 1000,
                    "endTime": day_window(REPORT_DATE)[1] * 1000,
                },
                company_id="1003",
            )
        )
        assert stats["data"] == {
            "totalCount": 0,
            "blockedCount": 0,
            "unblockedCount": 0,
            "observedCount": 0,
        }


def test_disabled_entity_fixture_keeps_business_code_separate_from_http() -> None:
    with running_mssp() as platform:
        response = platform.get(PATH_THREAT_RESPONSE_MODE, company_id="1004")
        assert response.status_code == 200
        assert response.json()["code"] == "AuthFailure.AutoDisposeAuthError"
        assert response.json()["message"]


@pytest.mark.parametrize(
    "company_id",
    ["2101", "2102", "2103", "2104", "2105", "2106", "2107", "2108", "2109", "2110", "2111"],
)
def test_scene_matrix_fixtures_are_deterministic(company_id: str) -> None:
    with running_mssp() as platform:
        current, previous = counters(platform, company_id)
        start, end = day_window(REPORT_DATE)

        if company_id == "2101":
            assert (current, previous) == (30, 10)
            assets = payload(
                platform.post(
                    PATH_ASSET_QUERY,
                    {
                        "order": "asc",
                        "offset": 0,
                        "limit": 100,
                        "first_time": [start * 1000, end * 1000],
                        "authorization_type": [1, 2, 3],
                        "service_status": [],
                        "company_id": company_id,
                    },
                    company_id=company_id,
                )
            )
            assert assets["data"]["total"] == 1
            new_ip = assets["data"]["list"][0]["asset"]
            assert new_ip == "10.20.1.10"
            rows = alerts(platform, company_id, REPORT_DATE)
            assert all(
                (row.get("hostIp") or {}).get("data", [{}])[0].get("renderValue") == new_ip
                for row in rows
            )
        elif company_id == "2102":
            assert (current, previous) == (30, 10)
            devices = payload(
                platform.post(
                    PATH_BRANCH_DEV,
                    {"type": 3, "status": 1, "offset": 0, "limit": 100, "company_id": company_id},
                    company_id=company_id,
                )
            )
            assert devices["data"]["total"] == 1
            gateway = devices["data"]["list"][0]["gateway_id"]
            rows = alerts(platform, company_id, REPORT_DATE)
            sources = {
                item["renderValue"]
                for row in rows
                for item in (row.get("devSourceNames") or {}).get("data") or []
            }
            assert any(gateway in source for source in sources)
        elif company_id == "2103":
            assert (current, previous) == (30, 10)
            current_sources = alert_sources(alerts(platform, company_id, REPORT_DATE))
            previous_sources = alert_sources(alerts(platform, company_id, PREVIOUS_DATE))
            assert current_sources == previous_sources
        elif company_id == "2104":
            assert (current, previous) == (30, 10)
            current_sources = alert_sources(alerts(platform, company_id, REPORT_DATE))
            previous_sources = alert_sources(alerts(platform, company_id, PREVIOUS_DATE))
            assert current_sources - previous_sources
        elif company_id == "2105":
            assert (current, previous) == (32, 30)
            assert alert_types(alerts(platform, company_id, REPORT_DATE)) == alert_types(
                alerts(platform, company_id, PREVIOUS_DATE)
            )
        elif company_id == "2106":
            assert (current, previous) == (30, 30)
            current_types = alert_types(alerts(platform, company_id, REPORT_DATE))
            previous_types = alert_types(alerts(platform, company_id, PREVIOUS_DATE))
            assert current_types == {"Web攻击", "暴力破解"}
            assert previous_types == {"Web攻击"}
        elif company_id == "2107":
            events = payload(
                platform.post(
                    PATH_EVENT_TABLE,
                    event_query_body(
                        company_id,
                        REPORT_DATE,
                        event_status=["disposal", "finished", "suspend", "inited"],
                        extra={"manage_type": [["STRATEGY_OPTIMIZE", "DEVICE_OFFLINE"]]},
                    ),
                    company_id=company_id,
                )
            )
            assert events["data"]["total"] == 1
            assert events["data"]["list"][0]["dev_name"] == "AF-2107"
        elif company_id == "2108":
            whitelist = payload(
                platform.post(
                    PATH_WHITELIST,
                    {
                        "order": {"update_time": "desc"},
                        "offset": 0,
                        "limit": 100,
                        "valid_time": [start * 1000, end * 1000],
                        "company_id": company_id,
                    },
                    company_id=company_id,
                )
            )
            assert whitelist["data"]["total"] == 1
            assert whitelist["data"]["list"][0]["name"] == "示例业务白名单"
        elif company_id == "2109":
            assert (current, previous) == (5, 30)
        elif company_id == "2110":
            assert (current, previous) == (25, 0)
        elif company_id == "2111":
            assert (current, previous) == (15, 10)


def test_fault_injection_is_one_shot_and_repeatable() -> None:
    with running_mssp() as platform:
        platform.inject(path="event_table", http_status=401, times=1)
        rejected = platform.post(PATH_EVENT_TABLE, event_query_body("1001", REPORT_DATE))
        assert rejected.status_code == 401
        assert rejected.json() == {"code": 1002, "msg": "登录态失效"}
        healthy = platform.post(PATH_EVENT_TABLE, event_query_body("1001", REPORT_DATE))
        assert healthy.status_code == 200
        assert healthy.json()["code"] == 0

        platform.inject(
            path=PATH_COMPANY_LIST,
            http_status=500,
            business_code=1005,
            msg="服务内部错误",
            times=1,
        )
        failed = platform.post(PATH_COMPANY_LIST, {"keyword": "", "offset": 0, "limit": 10})
        assert failed.status_code == 500
        assert failed.json() == {"code": 1005, "msg": "服务内部错误"}
        platform.reset()
        assert platform.post(PATH_COMPANY_LIST, {"keyword": "", "offset": 0, "limit": 10}).status_code == 200


def test_delay_injection_drives_a_client_timeout() -> None:
    with running_mssp() as platform:
        platform.inject(path=PATH_COMPANY_LIST, delay_ms=200, times=1)
        started = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            platform.post(
                PATH_COMPANY_LIST,
                {"keyword": "", "offset": 0, "limit": 10},
                timeout=0.05,
            )
        assert time.monotonic() - started < 2
        assert platform.post(PATH_COMPANY_LIST, {"keyword": "", "offset": 0, "limit": 10}).status_code == 200


def test_wecom_write_operations_are_off_by_default() -> None:
    with running_mssp() as platform:
        query = platform.post(PATH_WECOM_QUERY, {"param_list": []})
        assert query.status_code == 403
        assert query.json()["code"] == 40300

        headers = platform.headers()
        headers.pop("Content-Type")
        upload = httpx.post(
            platform.base_url + PATH_ATTACHMENT_UPLOAD,
            files={"file": ("report.txt", b"report", "text/plain")},
            data={"id": "WU_FILE_3"},
            headers=headers,
            timeout=5,
        )
        assert upload.status_code == 403
        assert upload.json()["code"] == 40300

        send = platform.post(
            PATH_WECOM_MESSAGE,
            {"company_name": {"_id": "1001"}, "wechat_group_name": {"_id": "wg-1001"}, "msg": "x"},
        )
        assert send.status_code == 403
        assert send.json()["code"] == 40300
        assert platform.stats()["writes"] == {"uploads": 0, "sends": 0, "duplicates": 0}

        platform.enable_wecom()
        assert platform.post(PATH_WECOM_QUERY, {"param_list": []}).json()["code"] == 0
        platform.enable_wecom(False)
        assert platform.post(PATH_WECOM_QUERY, {"param_list": []}).status_code == 403


def test_enabled_wecom_uploads_once_and_sends_once_per_trace_id() -> None:
    with running_mssp("--enable-wecom-write") as platform:
        assert platform.mssp_ready["wecomWrite"] is True
        query = payload(platform.post(PATH_WECOM_QUERY, {"param_list": []}))
        group = query["data"]["list"][0]["actual_data"]["actual_data_value"]
        assert group["_id"] == "wg-1001"
        assert group["group_name"] == "【示例】华东制造集团服务群"

        headers = platform.headers()
        headers.pop("Content-Type")
        upload_ids = []
        for _ in range(2):
            response = httpx.post(
                platform.base_url + PATH_ATTACHMENT_UPLOAD,
                files={"file": ("report.txt", b"same-bytes", "text/plain")},
                data={"id": "WU_FILE_3"},
                headers=headers,
                timeout=5,
            )
            assert response.status_code == 200
            upload_ids.append(response.json()["data"]["file"]["_id"])
        assert upload_ids[0] == upload_ids[1]
        assert platform.stats()["writes"]["uploads"] == 1

        send_body = {
            "company_name": {"_id": "1001", "value": "华东制造集团", "param_codes": "mss_customer_list"},
            "wechat_group_name": {
                "_id": group["_id"],
                "value": group["group_name"],
                "param_codes": "wechat_group_name",
            },
            "msg": "日报正文",
            "attachments": [upload_ids[0]],
            "is_async": True,
            "action_code": "wechat_push_group_msg",
        }
        first = payload(platform.post(PATH_WECOM_MESSAGE, send_body))
        assert first["msg"] == "success"
        duplicate = payload(platform.post(PATH_WECOM_MESSAGE, send_body))
        assert duplicate["msg"] == "duplicate"
        stats = platform.stats()["writes"]
        assert stats == {"uploads": 1, "sends": 1, "duplicates": 1}

        other_trace = platform.headers()
        other_trace["Traceid"] = "another-trace"
        second = payload(platform.post(PATH_WECOM_MESSAGE, send_body, headers=other_trace))
        assert second["msg"] == "success"
        assert platform.stats()["writes"] == {"uploads": 1, "sends": 2, "duplicates": 1}


def test_fixture_responses_repeat_byte_for_byte() -> None:
    with running_mssp() as platform:
        for path, body in (
            (PATH_SERVICE_INFO, {"offset": 0, "limit": 100, "company_id": "1001"}),
            (PATH_EVENT_TABLE, event_query_body("1001", REPORT_DATE)),
        ):
            first = platform.post(path, body)
            second = platform.post(path, body)
            assert first.json() == second.json()
        first = platform.post(PATH_XDR_ALERT_TABLE, xdr_query_body(REPORT_DATE))
        second = platform.post(PATH_XDR_ALERT_TABLE, xdr_query_body(REPORT_DATE))
        assert first.json() == second.json()
