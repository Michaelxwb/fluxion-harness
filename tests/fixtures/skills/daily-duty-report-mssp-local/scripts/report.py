#!/usr/bin/env python3
"""Report logic shared by ``scripts/run.py`` and ``scripts/cron_entry.py``.

The report is intentionally a *simplified* daily duty report: head counts,
external-threat trend, threat entities and security events, plus an attachment
placeholder. It keeps the data-fetch shape of ``daily-duty-report-mssp`` so the
same fake MSSP can drive SKILL_PLAN (argv/stdin) and AGENT_TURN flows.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import shared
from shared import FetchError

TZ = dt.timezone(dt.timedelta(hours=8))

FULL_ASSET_CODES = {"S-01-01-020", "S-01-01-022", "S-01-01-023"}
FULL_ASSET_SCENES = {
    "EMERGENCY_RESPONSE",
    "INTRANET_THREAT",
    "INTERNET_THREAT",
    "VULNERABILITY",
    "OTHER",
}
NON_FULL_SEC_SCENES = {"EMERGENCY_RESPONSE", "INTRANET_THREAT", "OTHER"}
STRATEGY_SUBS = {
    "DEVICE_OFFLINE",
    "AF_STRATEGY_OPTIMIZE",
    "EDR_STRATEGY_OPTIMIZE",
    "SIP_STRATEGY_OPTIMIZE",
}
SETTLED_STATUS = ["disposal", "suspend", "finished", "accept_risk", "announced", "protected"]
OFFLINE_STATUS = ["disposal", "finished", "suspend", "inited"]
FALSE_POSITIVE_KEYS = ("技术误报", "误推送")
STATUS_TEXT = {
    "inited": "未处置",
    "disposal": "处置中",
    "suspend": "定期跟进",
    "finished": "已闭环",
    "accept_risk": "不处置",
    "announced": "已通告",
    "protected": "已防护",
}
PAGE_LIMIT = 100
MAX_PAGES = 20
NO_GROUP_TEXT = "未获取到企业微信群，请联系管理员处理"
CLOSING = "安全运营中心将对您的资产进行持续保护，若有问题可在微信服务群反馈。"


def log(message: str) -> None:
    shared.log(message)


def yesterday() -> str:
    return (dt.datetime.now(TZ).date() - dt.timedelta(days=1)).isoformat()


def day_bounds(date_str: str) -> tuple[int, int]:
    try:
        day = dt.date.fromisoformat(date_str)
    except ValueError as exc:
        raise FetchError("--date 必须是 YYYY-MM-DD 格式") from exc
    start = dt.datetime.combine(day, dt.time(0, 0, 0), tzinfo=TZ)
    end = dt.datetime.combine(day, dt.time(23, 59, 59), tzinfo=TZ)
    return int(start.timestamp()), int(end.timestamp())


def previous_date(date_str: str) -> str:
    day = dt.date.fromisoformat(date_str) - dt.timedelta(days=1)
    return day.isoformat()


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #

def resolve_company_name(company_id: str) -> str:
    try:
        data = shared.post_json(
            "company_list", {"keyword": "", "offset": 0, "limit": PAGE_LIMIT}
        )
    except FetchError:
        return company_id
    for row in ((data.get("data") or {}).get("list") or []):
        if str(row.get("company_id")) == str(company_id):
            return str(row.get("company_name") or company_id)
    return company_id


def is_full_asset(company_id: str) -> bool:
    offset = 0
    while True:
        data = shared.post_json(
            "service_info",
            {"offset": offset, "limit": PAGE_LIMIT, "company_id": str(company_id)},
            company_id=company_id,
        )
        rows = ((data.get("data") or {}).get("list") or [])
        for item in rows:
            code = str(item.get("main_service_code") or item.get("service_code") or "")
            if (
                code in FULL_ASSET_CODES
                and str(item.get("auth_status")) == "1"
                and item.get("service_status") == "running"
            ):
                return True
        if len(rows) < PAGE_LIMIT:
            return False
        offset += PAGE_LIMIT


def security_log_count(company_id: str, date_str: str) -> int:
    tables = shared.post_json("ck_table_query", {"tableType": True})
    rows = ((tables.get("data") or {}).get("list") or [])
    by_name = {str(row.get("tableName")): int(row.get("id")) for row in rows}
    wanted = ["网络安全日志", "终端安全日志"]
    missing = [name for name in wanted if name not in by_name]
    if missing:
        raise FetchError(f"数据湖缺少日志表：{'、'.join(missing)}")
    start_sec, end_sec = day_bounds(date_str)
    from_text = dt.datetime.fromtimestamp(start_sec, TZ).strftime("%Y-%m-%d %H:%M:%S")
    to_text = dt.datetime.fromtimestamp(end_sec, TZ).strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    for name in wanted:
        data = shared.post_json(
            "ck_count",
            {
                "fromDate": from_text,
                "toDate": to_text,
                "tableId": [by_name[name]],
                "searchString": "",
                "constrains": [
                    {
                        "fieldAlias": "客户",
                        "fieldName": "customer",
                        "fieldOperater": "=",
                        "fieldValue": str(company_id),
                    }
                ],
                "quickSearch": {},
                "datalakeClusterName": "",
            },
            company_id=company_id,
        )
        total += int((data.get("data") or {}).get("total") or 0)
    return total


def _xdr_count_body(start_sec: int, end_sec: int) -> dict[str, Any]:
    return {
        "serviceInfo": {
            "appName": "incident",
            "servletContextPath": "/",
            "serviceType": "table",
            "handler": "alertTableQueryHandler",
        },
        "globalCondition": {
            "time": {
                "timeField": "lastTime",
                "begin": {"type": "absolute", "value": int(start_sec)},
                "end": {"type": "absolute", "value": int(end_sec)},
            },
            "branchIds": None,
            "platformHostBranchIds": None,
            "regionIds": None,
        },
        "spl": {
            "originalSpl": "",
            "mappedSpl": "",
            "extensionParams": {
                "frontRender": [],
                "mappedInputSpl": "",
                "originalInputSpl": "",
            },
        },
        "table": {
            "viewName": "AlertView",
            "pageSize": 10,
            "pageNum": 1,
            "tableFields": [],
            "aggregationStrategies": None,
            "extensionParams": {},
        },
        "extensionParams": None,
        "model": "expert",
        "viewInstanceId": "",
        "autoRefresh": None,
        "enableHistory": None,
        "readCache": False,
        "writeCache": False,
    }


def alert_count(company_id: str, date_str: str) -> int:
    start_sec, end_sec = day_bounds(date_str)
    data = shared.post_json_raw(
        "xdr_alert_count",
        _xdr_count_body(start_sec, end_sec),
        company_id=company_id,
    )
    total = (data.get("data") or {}).get("total")
    if total is None:
        raise FetchError("XDR 告警计数响应里没有 data.total")
    return int(total)


def _event_body(
    company_id: str,
    date_str: str,
    *,
    event_status: list[str],
    service_status: list[int],
    offset: int,
) -> dict[str, Any]:
    start_sec, end_sec = day_bounds(date_str)
    return {
        "order": {},
        "offset": offset,
        "limit": PAGE_LIMIT,
        "asset_type": -1,
        "hw_status": -1,
        "protection_type": [],
        "company_id": [str(company_id)],
        "create_time": [start_sec * 1000, end_sec * 1000],
        "event_name": "",
        "event_id": "",
        "event_status": list(event_status),
        "service_status": list(service_status),
        "priority": "all",
        "event_grading_tags": [],
        "scene": "event_default",
    }


def _fetch_event_pages(
    company_id: str,
    date_str: str,
    *,
    event_status: list[str],
    service_status: list[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(MAX_PAGES):
        data = shared.post_json(
            "event_table",
            _event_body(
                company_id,
                date_str,
                event_status=event_status,
                service_status=service_status,
                offset=offset,
            ),
            company_id=company_id,
        )
        page = ((data.get("data") or {}).get("list") or [])
        rows.extend(page)
        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    return rows


def _event_valid(row: dict[str, Any], full_asset: bool) -> bool:
    manage_type = str(row.get("manage_type") or "")
    if row.get("event_name") == "AF存在直通策略":
        return True
    if full_asset:
        if int(row.get("service_status") or 0) == 0 and manage_type in FULL_ASSET_SCENES:
            return True
        if manage_type == "UNDECLARED_THREAT" and int(row.get("push_tag") or 0) == 1:
            return True
        return manage_type == "STRATEGY_OPTIMIZE" and row.get("manage_sub_type") == "DEVICE_OFFLINE"
    if manage_type in NON_FULL_SEC_SCENES:
        return True
    if manage_type == "INTERNET_THREAT":
        return True
    if manage_type == "UNDECLARED_THREAT":
        return True
    return (
        manage_type == "STRATEGY_OPTIMIZE"
        and str(row.get("manage_sub_type") or "") in STRATEGY_SUBS
    )


def _remark_text(company_id: str, event_id: str) -> str:
    data = shared.post_json(
        "event_remark",
        {"order": "desc", "offset": 0, "limit": 10, "event_id": event_id},
        company_id=company_id,
    )
    parts = []
    for item in ((data.get("data") or {}).get("list") or []):
        text = str(item.get("remark_txt") or "")
        text = " ".join(text.replace("<p>", " ").replace("</p>", " ").split())
        if text:
            parts.append(text)
    return " ".join(parts)


def _drop_false_positives(
    company_id: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    kept = []
    for row in rows:
        if row.get("event_status") != "accept_risk":
            kept.append(row)
            continue
        try:
            remark = _remark_text(company_id, str(row.get("event_id") or ""))
        except FetchError as exc:
            log(f"[警告] 备注查询失败，保留不处置事件：{exc}")
            kept.append(row)
            continue
        if any(key in remark for key in FALSE_POSITIVE_KEYS):
            log(f"[过滤] 排除误报事件 {row.get('event_id')}")
            continue
        kept.append(row)
    return kept


def fetch_events(company_id: str, date_str: str, full_asset: bool) -> list[dict[str, Any]]:
    service_status = [] if full_asset else [0, 1, 3]
    rows = _fetch_event_pages(
        company_id,
        date_str,
        event_status=SETTLED_STATUS,
        service_status=service_status,
    )
    if not full_asset:
        rows += _fetch_event_pages(
            company_id,
            date_str,
            event_status=["inited"],
            service_status=[3],
        )
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("event_id") or "")
        if event_id and event_id not in seen and _event_valid(row, full_asset):
            seen[event_id] = row
    kept = _drop_false_positives(company_id, list(seen.values()))
    return sorted(kept, key=lambda row: str(row.get("create_time") or ""))


def _entity_objects(company_id: str, date_str: str, mode: str) -> list[dict[str, Any]]:
    start_sec, end_sec = day_bounds(date_str)
    filter_name = "blockStatus" if mode == "security" else "gptBlockResult"
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        data = shared.post_json(
            "threat_objects",
            {
                "startTime": start_sec * 1000,
                "endTime": end_sec * 1000,
                filter_name: ["blocked"],
                "pageNum": page,
                "pageSize": PAGE_LIMIT,
            },
            company_id=company_id,
        )
        payload = data.get("data") or {}
        items = payload.get("data") or []
        rows.extend(items)
        if len(items) < PAGE_LIMIT or len(rows) >= int(payload.get("total") or 0):
            break
    return sorted(
        rows,
        key=lambda item: (
            -int((item.get("blockHistory") or {}).get("blockCount") or 0),
            -int(item.get("updateTimestamp") or 0),
        ),
    )


def fetch_entities(company_id: str, date_str: str) -> dict[str, Any]:
    response = shared.get_json("threat_response_mode", company_id=company_id)
    code = response.get("code")
    if code == "AuthFailure.AutoDisposeAuthError":
        return {"mode": "disabled", "detail": "未开通智能对抗服务", "stats": None, "objects": []}
    if code != "Success":
        mode = "security"
        detail = f"取数不可用（code={code}），按对抗模式兜底"
    else:
        mode = str((response.get("data") or {}).get("responseMode") or "security").lower()
        detail = ""
    if mode not in ("security", "mock"):
        mode = "security"
    start_sec, end_sec = day_bounds(date_str)
    stats = {"total": 0, "banned": 0, "allowed": 0, "monitored": 0}
    try:
        data = shared.post_json(
            "threat_entity_statistics",
            {"startTime": start_sec * 1000, "endTime": end_sec * 1000},
            company_id=company_id,
        )
        payload = data.get("data") or {}
        stats = {
            "total": int(payload.get("totalCount") or 0),
            "banned": int(payload.get("blockedCount") or 0),
            "allowed": int(payload.get("unblockedCount") or 0),
            "monitored": int(payload.get("observedCount") or 0),
        }
    except FetchError as exc:
        log(f"[警告] 威胁实体 statistics 取数失败：{exc}")
    try:
        objects = _entity_objects(company_id, date_str, mode)
    except FetchError as exc:
        log(f"[警告] 威胁实体明细取数失败：{exc}")
        objects = []
    return {"mode": mode, "detail": detail, "stats": stats, "objects": objects}


def query_wecom_group(company_id: str) -> str | None:
    try:
        data = shared.post_json(
            "wechat_group_query",
            {
                "param_list": [
                    {
                        "param_code": "wechat_group_name",
                        "other_param": {"company_id": str(company_id)},
                    }
                ]
            },
            company_id=company_id,
        )
    except FetchError as exc:
        log(f"[企微] 查群失败：{exc}")
        return None
    rows = ((data.get("data") or {}).get("list") or [])
    if len(rows) != 1:
        return None
    value = ((rows[0].get("actual_data") or {}).get("actual_data_value") or {})
    name = str(value.get("group_name") or "").strip()
    return name or None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _trend_label(rate: float) -> str:
    if rate >= 150:
        return "显著上升"
    if rate >= 50:
        return "明显上升"
    if rate >= 20:
        return "上升"
    if rate > -20:
        return "基本稳定"
    if rate > -50:
        return "下降"
    return "明显下降"


def _pct(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def threat_text(current: int, previous: int) -> str:
    if current == 0 and previous == 0:
        return "两期均未发现外部威胁。"
    if previous == 0:
        return f"本期共发现外部威胁 {current} 起，上期为零，本期为新增。"
    delta = current - previous
    rate = delta / previous * 100
    if delta > 0:
        return (
            f"本期共发现外部威胁 {current} 起，较上一统计周期增加 {delta} 起，"
            f"增幅 {_pct(rate)}%，整体呈{_trend_label(rate)}趋势。"
        )
    if delta < 0:
        return (
            f"本期共发现外部威胁 {current} 起，较上一统计周期减少 {abs(delta)} 起，"
            f"降幅 {_pct(abs(rate))}%，整体呈{_trend_label(rate)}趋势。"
        )
    return f"本期共发现外部威胁 {current} 起，较上一统计周期持平。"


def _entity_name(item: dict[str, Any]) -> str:
    value = item.get("threatObject")
    if isinstance(value, dict):
        return str(value.get("threatObjectName") or "（未知实体）")
    return str(value or "（未知实体）")


def entity_text(entity: dict[str, Any]) -> str:
    if entity["mode"] == "disabled":
        return "暂不具备智能对抗能力，待进一步跟进。"
    stats = entity.get("stats") or {}
    head = (
        f"已从告警中提取与外部威胁有关的IP和域名共 {stats.get('total', 0)} 个，"
        f"建议封禁的威胁实体 {stats.get('banned', 0)} 个，"
        f"建议放行的业务IP {stats.get('allowed', 0)} 个，"
        f"持续监测的对象 {stats.get('monitored', 0)} 个。"
    )
    objects = (entity.get("objects") or [])[:5]
    if not objects:
        return head
    title = "已封堵的IP/域名如下：" if entity["mode"] == "security" else "建议封禁的IP/域名如下："
    lines = [head + " " + title]
    for index, item in enumerate(objects, start=1):
        lines.append(f"{index}）{_entity_name(item)}")
    return "\n".join(lines)


def event_text(rows: list[dict[str, Any]], attachment: str) -> str:
    if not rows:
        return "暂未发现攻击成功或资产失陷迹象。"
    lines = [f"云端服务团队分析和处置安全事件{len(rows)} 起："]
    for index, row in enumerate(rows[:5], start=1):
        ip = str(row.get("host_ip") or row.get("real_ip") or "")
        name = str(row.get("event_name") or "（未命名事件）")
        status = STATUS_TEXT.get(str(row.get("event_status") or ""), str(row.get("event_status") or ""))
        lines.append(" ".join(part for part in (f"{index}）{ip}", name, status) if part))
    if len(rows) > 5:
        lines.append(f"更多安全事件请查阅《{attachment}》")
    return "\n".join(lines)


def output_root() -> Path:
    env = os.environ.get("SKILL_OUTPUT_DIR", "").strip()
    if env:
        return Path(env)
    return Path.cwd() / ".skill-output"


def _safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in (name or "客户") if ch not in r'\/:*?"<>|').strip()
    return cleaned or "客户"


def _archive_dir(company_name: str, company_id: str, date_str: str) -> Path:
    return (
        output_root()
        / "值守日报存档"
        / f"{_safe_name(company_name)}_{company_id}"
        / date_str
    )


def _write_outputs(
    company_name: str,
    company_id: str,
    date_str: str,
    body: str,
    attachment_name: str,
    *,
    event_count: int,
    entity_count: int,
) -> tuple[Path, Path]:
    target = _archive_dir(company_name, company_id, date_str)
    target.mkdir(parents=True, exist_ok=True)
    report_path = target / "report.txt"
    report_path.write_text(body + "\n", encoding="utf-8")
    attachment_path = target / attachment_name
    attachment_path.write_text(
        "本地回归占位附件（不是真实 xlsx）\n"
        f"客户：{company_name}（{company_id}）\n"
        f"日期：{date_str}\n"
        f"安全事件：{event_count} 起\n"
        f"威胁实体：{entity_count} 个\n",
        encoding="utf-8",
    )
    return report_path, attachment_path


def generate(
    company_id: str,
    date_str: str | None = None,
    *,
    no_wecom_push: bool = False,
) -> dict[str, Any]:
    date_str = date_str or yesterday()
    log(f"[客户] {company_id} / {date_str}")
    company_name = resolve_company_name(company_id)
    try:
        full_asset = is_full_asset(company_id)
    except FetchError as exc:
        raise FetchError(f"客户服务项查询失败：{exc}") from exc
    log(f"[客户] {company_name} → {'全资产' if full_asset else '非全资产'}")

    missing: list[str] = []

    try:
        log_count = security_log_count(company_id, date_str)
    except FetchError as exc:
        log_count = 0
        missing.append("安全日志数量")
        log(f"[取数缺项] 安全日志：{exc}")

    try:
        current_alerts = alert_count(company_id, date_str)
        previous_alerts = alert_count(company_id, previous_date(date_str))
    except FetchError as exc:
        current_alerts = previous_alerts = 0
        missing.append("安全告警数量")
        missing.append("外部威胁")
        log(f"[取数缺项] 安全告警：{exc}")

    try:
        rows = fetch_events(company_id, date_str, full_asset)
    except FetchError as exc:
        rows = []
        missing.append("有效事件数量")
        log(f"[取数缺项] 有效事件：{exc}")

    try:
        entity = fetch_entities(company_id, date_str)
    except FetchError as exc:
        entity = {"mode": "security", "detail": "", "stats": None, "objects": []}
        missing.append("威胁实体处置")
        log(f"[取数缺项] 威胁实体：{exc}")

    if "外部威胁" in missing:
        threat_section = "本期外部威胁数据暂不可用。"
    else:
        threat_section = threat_text(current_alerts, previous_alerts)

    tail = ""
    if not no_wecom_push:
        group_name = query_wecom_group(company_id)
        if group_name:
            tail = (
                f"是否将上述内容通过企业微信发送到服务群【{group_name}】，"
                "回复“是”将自动发送"
            )
        else:
            tail = NO_GROUP_TEXT

    attachment_name = f"安全事件及威胁实体封堵列表-{date_str.replace('-', '')}.txt"
    body = build_body(
        date_str=date_str,
        log_count=log_count,
        alert_count=current_alerts,
        event_count=len(rows),
        threat_section=threat_section,
        entity_section=entity_text(entity),
        event_section=event_text(rows, attachment_name),
        missing=missing,
        attachment_name=attachment_name,
    )
    report_path, attachment_path = _write_outputs(
        company_name,
        company_id,
        date_str,
        body,
        attachment_name,
        event_count=len(rows),
        entity_count=len(entity.get("objects") or []),
    )
    return {
        "body": body,
        "tail": tail,
        "report_path": str(report_path),
        "attachment_path": str(attachment_path),
        "attachment_name": attachment_name,
        "company_id": str(company_id),
        "company_name": company_name,
        "full_asset": full_asset,
        "log_count": log_count,
        "alert_count": current_alerts,
        "previous_alert_count": previous_alerts,
        "event_count": len(rows),
        "entity_mode": entity["mode"],
    }


def build_body(
    *,
    date_str: str,
    log_count: int,
    alert_count: int,
    event_count: int,
    threat_section: str,
    entity_section: str,
    event_section: str,
    missing: list[str],
    attachment_name: str,
) -> str:
    compact = date_str.replace("-", "")
    parts = [
        f"【深信服MSS每日总结-{compact}】",
        f"尊敬的客户，您好，{date_str} 00:00:00 至 {date_str} 23:59:59 安全运营值守情况如下：",
        (
            f"安全运营中心主动分析组件上报安全日志{log_count}条，"
            f"分析研判安全告警{alert_count}条，"
            f"生成有效事件{event_count}条，详情如下："
        ),
        "",
        "1、外部威胁：",
        threat_section,
        "",
        "2、威胁实体处置：",
        entity_section,
        "",
        "3、安全事件：",
        event_section or "暂未发现攻击成功或资产失陷迹象。",
        "",
        f"附件：《{attachment_name}》（本地回归占位，未生成实际工作簿）",
    ]
    if missing:
        parts.extend(["", f"本期以下项目取数暂不可用：{'、'.join(missing)}。"])
    parts.extend(["", CLOSING])
    return "\n".join(parts)


def fatal(message: str) -> int:
    sys.stderr.write(
        json.dumps(
            {
                "error": {
                    "code": "MSSP_FETCH_FAILED",
                    "message": message,
                    "retryable": False,
                }
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 2
