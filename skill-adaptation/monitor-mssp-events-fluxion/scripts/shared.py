# -*- coding: utf-8 -*-
"""
monitor-mssp-events 共享模块。

- 登录态：session-manager 经 MUAD_SESSION_KEY 判定身份后返回登录态文件，
  从中提取 mssp 平台 cookie（沿用 policy-check shared 的约定）。
- 配置：config/api_config.json 集中管理 origin / host_header / endpoints，
  origin 支持环境变量覆盖（MONITOR_MSSP_BASE_URL）。
- 不写凭据到 stdout / 日志。
"""

import json
import os
import subprocess
import sys
import time
import uuid

try:
    import requests
except ImportError:
    requests = None

SKILL_NAME = "monitor-mssp-events"
_SESSION_PROFILE = {}

# 当前仅实现 MSSP（SOAR）
PLATFORM_MSSP = "mssp"

MAX_RETRIES = 3
RETRY_DELAY = 2

# 登录失效业务码：接口可能返回 HTTP 200 但 body.code 为以下值时表示登录态失效。
LOGIN_EXPIRED_CODES = (401, 403)


def log(msg: str, level: str = "INFO") -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


def _load_api_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "..", "config", "api_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_host_header() -> str:
    return str(_SESSION_PROFILE.get("hostHeader", {}).get("Host", ""))


def get_origin(platform: str = PLATFORM_MSSP) -> str:
    base = _SESSION_PROFILE.get("baseUrl")
    if not isinstance(base, str) or not base.startswith(("http://", "https://")):
        raise RuntimeError("平台登录态缺少有效业务地址")
    return base.rstrip("/")


def get_referer(platform: str = PLATFORM_MSSP) -> str:
    return str(_SESSION_PROFILE.get("referer") or get_origin(platform) + "/index.html")


def get_platform_setting(key: str, platform: str = PLATFORM_MSSP):
    headers = _SESSION_PROFILE.get("staticHeaders", {})
    lookup = {"timezone": "timezone", "fixed_csrf_token": "x-csrftoken"}.get(key, key.lower())
    return next((v for k, v in headers.items() if k.lower() == lookup), None)


def get_endpoint(key: str, platform: str = PLATFORM_MSSP) -> str:
    cfg = _load_api_config()
    value = cfg.get("platforms", {}).get(platform, {}).get("endpoints", {}).get(key)
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError(f"无效的平台接口路径: {key}")
    return get_origin(platform) + value


def extract_cookie_value(cookie: str, key: str):
    if not cookie:
        return None
    for item in cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return None


def get_cookie(platform: str = PLATFORM_MSSP) -> str:
    """经 session-manager 取指定平台 cookie。身份由 guard 注入的 MUAD_SESSION_KEY 决定。"""
    cmd = ["session-manager", "get-state", "--skill-name", SKILL_NAME]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        raise RuntimeError(f"调用 session-manager 失败: {e}") from e

    if result.returncode != 0:
        raise RuntimeError(f"获取登录态失败: {(result.stderr or result.stdout or '').strip()}")

    try:
        state = json.loads(result.stdout)
    except Exception as e:
        raise RuntimeError(f"session-manager 输出解析失败: {e}") from e

    session_file = state.get("sessionStateFile")
    if not session_file or not os.path.exists(session_file):
        raise RuntimeError("未获取到登录态文件，请先确认 mssp 平台已绑定并登录")

    try:
        with open(session_file, "r", encoding="utf-8") as f:
            session = json.load(f)
    except Exception as e:
        raise RuntimeError(f"读取登录态文件失败: {e}") from e

    section = (session or {}).get("platforms", {}).get(platform)
    global _SESSION_PROFILE
    _SESSION_PROFILE = (section or {}).get("requestProfile") or {}
    cookies = (section or {}).get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise RuntimeError(f"登录态缺少 {platform} 平台 cookie")

    pairs = []
    for c in cookies:
        if isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("value"), str):
            pairs.append(f"{c['name']}={c['value']}")
    if not pairs:
        raise RuntimeError(f"登录态 {platform} 平台的 cookie 无效")
    return "; ".join(pairs)


def build_headers(cookie: str, platform: str = PLATFORM_MSSP) -> dict:
    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "application/json",
        "cookie": cookie,
        "referer": get_referer(platform),
        "traceid": str(uuid.uuid4()),
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    host = get_host_header()
    if host:
        headers["host"] = host
    headers.update(_SESSION_PROFILE.get("staticHeaders", {}))
    # CSRF 头：从 cookie 提取的动态 X-Csrftoken / x-csrf-token
    csrf = extract_cookie_value(cookie, "csrf_token")
    if csrf:
        headers["X-Csrftoken"] = csrf
    # MSSP 平台固定头：X-CSRFToken + timezone
    if platform == PLATFORM_MSSP:
        fixed = get_platform_setting("fixed_csrf_token", platform)
        tz = get_platform_setting("timezone", platform) or "+08:00"
        if fixed:
            headers["X-CSRFToken"] = fixed
        headers["timezone"] = tz
    return headers


def header_refresher(platform: str = PLATFORM_MSSP):
    """返回一个无参回调：重取登录态 cookie 并重建请求头，供 http_json 遇登录失效时刷新。"""
    def _rebuild() -> dict:
        return build_headers(get_cookie(platform), platform)
    return _rebuild


def search_customers(cookie: str, keyword: str, limit: int = 20, platform: str = PLATFORM_MSSP) -> list:
    """按关键词模糊搜索当前登录态**在线**客户。

    返回 data.list（仅 service_status=1 的在线客户），每项含 company_id / company_name / pms_customer_name。
    已过滤：已过期、未上线的客户（service_status=1 即在线）；展示层的疑似非正式客户再二次排除。
    真实响应结构：{"code":0,"data":{"list":[{company_id,company_name,pms_customer_name,...}]}}
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        raise ValueError("客户搜索必须提供非空关键词")
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("客户搜索 limit 必须是 1 到 100 的整数")
    url = get_endpoint("customer_search", platform)
    headers = build_headers(cookie, platform)
    payload = {
        "order": "asc", "offset": 0, "limit": limit, "keyword": keyword,
        "share_ids": [], "delivery_channel_id": [], "service_code": [], "industry": [],
        "industry_segmentation": [], "customer_type": [], "customer_stratification": [],
        "protection_type": [], "service_group": [], "delivery_method": [], "platform_type": [],
        "service_status": 1, "my_customer": 0,  # 1=在线客户，过滤过期/未上线
    }
    resp = http_json("POST", url, headers, payload=payload)
    try:
        data = resp.json()
    except Exception as e:
        log(f"customer_search 响应解析失败: {e}", "ERROR")
        return []
    if not isinstance(data, dict) or data.get("code") != 0:
        log(f"customer_search 返回异常 code={data.get('code') if isinstance(data, dict) else '?'}", "WARNING")
        return []
    body = data.get("data") or {}
    lst = body.get("list") if isinstance(body, dict) else None
    if not isinstance(lst, list):
        return []
    # 展示层二次排除疑似非正式客户（影子/学员实训/测试等）
    no_show_kw = ("[影子]", "学员实训", "（学员实训）", "测试")
    return [
        c for c in lst
        if isinstance(c, dict)
        and not any(k in str(c.get("company_name") or "") for k in no_show_kw)
    ]


def pick_exact_customer(customers: list, keyword: str):
    if not isinstance(customers, list) or not customers:
        return None
    if len(customers) == 1:
        return customers[0]
    kw = (keyword or "").strip()
    for c in customers:
        if not isinstance(c, dict):
            continue
        cands = [c.get("company_name"), c.get("pms_customer_name"), c.get("company_id")]
        if any((str(x or "").strip() == kw) for x in cands):
            return c
    return None


def get_event_detail(cookie: str, event_id: str, platform: str = PLATFORM_MSSP) -> dict:
    """获取单个事件的详情。返回 data（dict），失败/异常返回空 dict。

    真实响应结构：{"code":0,"data":{"base_info":{...}, ...}}。
    关键字段：data.base_info.device_type（69=NGES设备）、dev_name、platform_type。
    """
    return get_event_base_info(cookie, event_id, platform)[0]


def get_event_base_info(cookie: str, event_id: str, platform: str = PLATFORM_MSSP):
    """获取单个事件详情并返回 (base_info, detail)。

    返回：
      base_info: 事件详情的 base_info（dict）；缺失/异常返回空 dict
      detail:    事件详情 data 全量（dict）；缺失/异常返回空 dict
    """
    event_id = str(event_id or "").strip()
    if not event_id:
        return {}, {}
    url = get_endpoint("event_detail", platform)
    headers = build_headers(cookie, platform)
    payload = {"event_id": event_id}
    try:
        resp = http_json("POST", url, headers, payload=payload)
        data = resp.json()
    except Exception as e:
        log(f"event_detail 查询失败 (event_id={event_id}): {e}", "WARNING")
        return {}, {}
    if not isinstance(data, dict) or data.get("code") != 0:
        log(f"event_detail 返回异常 code={data.get('code') if isinstance(data, dict) else '?'} (event_id={event_id})", "WARNING")
        return {}, {}
    body = data.get("data")
    if not isinstance(body, dict):
        return {}, {}
    base = body.get("base_info")
    return (base if isinstance(base, dict) else {}), body


def get_event_nges(cookie: str, event_id: str, platform: str = PLATFORM_MSSP, snapshot=None):
    """判断事件是否来自 NGES（杀毒引擎）设备。返回 (is_nges, device_type, dev_name)。

    依据：事件详情 base_info.device_type == 69 即为 NGES 设备。
    dev_name 如 "SaaS_NGES"（NGES）/ "STA_001"（STA）/ "SIP_001"（SIP）。

    注意：本函数只判「NGES 数据源」，不代表主机是否被 NGES 覆盖。
    """
    base, _detail = get_event_base_info(cookie, event_id, platform)
    if snapshot is not None and _detail:
        snapshot["detail"] = _detail
    if not isinstance(base, dict) or not base:
        return False, None, ""
    raw_dt = base.get("device_type")
    try:
        dt = int(raw_dt) if raw_dt is not None and raw_dt != "" else None
    except (TypeError, ValueError):
        dt = None
    dev = base.get("dev_name") or ""
    is_nges = (dt == 69)
    return is_nges, dt, dev


def _adapter_device_names(adapter) -> list:
    """提取资产 adapter 中所有设备名（含键名兜底）。"""
    names = []
    if not isinstance(adapter, dict):
        return names
    for product, devices in adapter.items():
        if isinstance(devices, list) and devices:
            for device in devices:
                if isinstance(device, dict):
                    name = device.get("dev_name") or device.get("product") or ""
                else:
                    name = device
                names.append(str(name or "").strip())
        else:
            names.append(str(product or "").strip())
    return [n for n in names if n]


def _adapter_has_nges_type(adapter) -> bool:
    """判断资产 adapter 中是否存在 NGES 类型设备（用于「设备覆盖含 NGES」过滤）。

    判定口径：只看 adapter 的**键/设备类型**（如 NGES、aES、STA），键名含 "NGES" 才算；
    **不看设备名 dev_name** —— 名字里带 nges 的（如 "nges-agent-01"）不算。

    例：{"NGES": [{"dev_name": "SaaS_NGES"}]} → True
        {"aES": [{"dev_name": "nges-agent-01"}]} → False
        {"aES": [...], "STA": [...], "NGES": [...]} → True（多个里有一个是 NGES 类型即可）
    """
    if not isinstance(adapter, dict):
        return False
    for product in adapter.keys():
        if "NGES" in str(product or "").upper():
            return True
    return False


def get_nges_coverage(cookie: str, host_ip: str, company_id: str, nges_id: str = "", nges_name: str = "",
                      platform: str = PLATFORM_MSSP, asset_info=None) -> bool:
    """判断某主机的「设备覆盖」中是否含 NGES 类型设备（用于「设备覆盖含 NGES」过滤）。

    判定口径：资产接口 adapter 的**键/设备类型**含 "NGES"（大小写不敏感）才算，
    有多个设备类型时只要其中一个含 NGES 即可；**不看设备名**（名字带 nges 不算）。

    nges_id / nges_name 为保留参数，不参与判定。
    """
    host_ip = str(host_ip or "").strip()
    if not host_ip:
        return False
    if asset_info is None:
        asset_info = get_asset_info(cookie, host_ip, company_id, platform)
    if not asset_info:
        return False
    return _adapter_has_nges_type(asset_info.get("adapter"))


def get_tenant_id(cookie: str, company_id: str, platform: str = PLATFORM_MSSP) -> str:
    """查询客户的 NGES 设备分支，返回其 gateway_id 作为 tenantId。

    接口：/gateway/customer-mgr-service/order/v1/branch/dev?_method=GET
    请求体固定字段（type=69 即 NGES 设备），拿 company_id 去查。
    返回 data 中首个含 gateway_id 的设备的 gateway_id（字符串）；无则返回空串。
    失败/异常不抛错，返回空串，不影响主监控流程。
    """
    company_id = str(company_id or "").strip()
    if not company_id or not company_id.isdigit():
        return ""
    url = get_endpoint("branch_dev", platform)
    headers = build_headers(cookie, platform)
    payload = {
        "order": "asc", "offset": 0, "limit": 100, "keyword": "",
        "type": 69, "status": 0, "company_id": company_id,
        "platform_type": "scloud", "belong_local_xdr": 0,
    }
    try:
        resp = http_json("POST", url, headers, payload=payload)
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        log(f"branch_dev 查询失败 (company_id={company_id}): {e}", "WARNING")
        return ""
    if not isinstance(data, dict) or data.get("code") != 0:
        log(f"branch_dev 返回异常 code={data.get('code') if isinstance(data, dict) else '?'} (company_id={company_id})", "WARNING")
        return ""
    body = data.get("data")
    rows = body.get("list") if isinstance(body, dict) else (body if isinstance(body, list) else None)
    if not isinstance(rows, list):
        return ""
    for item in rows:
        if not isinstance(item, dict):
            continue
        gateway_id = item.get("gateway_id")
        if gateway_id is not None and str(gateway_id).strip():
            return str(gateway_id).strip()
    return ""


def snapshot_page_complete(items, body, limit):
    """只有原查询页已覆盖候选集时，下游才可复用不同 limit 的查询。"""
    total = body.get("total")
    if total is None:
        return len(items) < limit
    try:
        return len(items) >= int(total)
    except (TypeError, ValueError):
        return False


def get_asset_info(cookie: str, host_ip: str, company_id: str, platform: str = PLATFORM_MSSP, snapshot=None) -> dict:
    """按主机 IP 和客户 ID 查询资产，返回首条资产及 NGES 覆盖信息。"""
    host_ip = str(host_ip or "").strip()
    company_id = str(company_id or "").strip()
    if not host_ip or any(char in host_ip for char in "\r\n"):
        return {}
    if not company_id.isdigit():
        raise ValueError("资产查询只接受平台返回的数字 company_id")
    url = get_endpoint("asset_query", platform)
    headers = build_headers(cookie, platform)
    payload = {
        "order": "asc", "offset": 0, "limit": 10, "service_status": [], "is_alive": -1,
        "ip_url_keyword": host_ip, "asset_type": [], "business_level": [], "first_time": [],
        "update_time": [], "keyword": "", "asset_tag": [], "agent_status": [],
        "af_defend_status": "", "database": [], "middleware": [], "os": [],
        "developing_languages": [], "development_framework": [], "server_port": [],
        "company_id": company_id, "asset_group_id": "all", "authorization_type": [],
    }
    try:
        resp = http_json("POST", url, headers, payload=payload)
        data = resp.json()
    except Exception as e:
        log(f"asset_query 查询失败 (ip={host_ip}): {e}", "WARNING")
        return {}
    if not isinstance(data, dict) or data.get("code") != 0:
        log(f"asset_query 返回异常 code={data.get('code') if isinstance(data, dict) else '?'} (ip={host_ip})", "WARNING")
        return {}
    body = data.get("data") or {}
    lst = body.get("list") if isinstance(body, dict) else None
    if not isinstance(lst, list) or not lst:
        return {}
    assets = [asset for asset in lst if isinstance(asset, dict)]
    if snapshot is not None:
        snapshot["assets"] = assets
        snapshot["asset_host_ip"] = host_ip
        snapshot["assets_complete"] = snapshot_page_complete(lst, body, 10)
    if not assets:
        return {}
    asset = dict(assets[0])
    raw_mac = asset.get("mac_address")
    if isinstance(raw_mac, list):
        asset["mac_address"] = "、".join(str(m).strip() for m in raw_mac if str(m).strip())
    elif raw_mac is None:
        asset["mac_address"] = ""
    else:
        asset["mac_address"] = str(raw_mac).strip()

    def _has_nges_adapter(item):
        return _adapter_has_nges_type(item.get("adapter") if isinstance(item, dict) else None)

    asset["nges_installed"] = any(_has_nges_adapter(item) for item in assets)
    return asset


def customer_display(c) -> str:
    """客户可读名称：优先 company_name，PMS 名不同则附注。"""
    name = str(c.get("company_name") or "")
    pms = str(c.get("pms_customer_name") or "")
    if pms and pms != "-" and pms != name:
        return f"{name}（{pms}）"
    return name


def _output_root() -> str:
    """可写输出/状态根目录：guard 注入的 SKILL_OUTPUT_DIR；缺失（本地/开发调试）回退到系统临时目录。

    Public Skill 运行时 skill 根目录是**只读挂载**，`state/` 不可写，所以事件去重等
    运行时可变状态统一写到 `SKILL_OUTPUT_DIR`（per-user 隔离，随用户持久）。
    """
    d = os.environ.get("SKILL_OUTPUT_DIR")
    if d:
        os.makedirs(d, exist_ok=True)
        return d
    import tempfile

    d = os.path.join(tempfile.gettempdir(), "monitor-mssp-events")
    os.makedirs(d, exist_ok=True)
    return d


def _state_dir() -> str:
    """事件去重状态目录。优先环境覆盖，其次输出根下的 state/。"""
    d = os.environ.get("MONITOR_STATE_DIR")
    if d:
        os.makedirs(d, exist_ok=True)
        return d
    d = os.path.join(_output_root(), "state")
    os.makedirs(d, exist_ok=True)
    return d


def _state_path() -> str:
    """去重状态文件路径（写 SKILL_OUTPUT_DIR/state）。"""
    return os.path.join(_state_dir(), "reminded_events.json")


def load_reminded() -> dict:
    """加载已提醒记录：{event_id: remind_ts_ms}。文件不存在视为空。"""
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"读取去重状态失败: {e}", "WARNING")
        return {}


def save_reminded(reminded: dict) -> None:
    """持久化已提醒记录。最多保留 7 天内的 event_id，防止无限增长。"""
    now = int(time.time() * 1000)
    cutoff = now - 7 * 24 * 3600 * 1000
    pruned = {k: v for k, v in reminded.items() if v and v >= cutoff}
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pruned, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"写入去重状态失败: {e}", "WARNING")


def _business_code(resp):
    """取响应体里的业务码；无 code 字段（如资产视图 success 结构）返回 None。"""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict) and "code" in data:
        return data.get("code")
    return None


def _is_login_expired(code) -> bool:
    return code in LOGIN_EXPIRED_CODES


def http_json(method: str, url: str, headers: dict, payload=None, timeout: int = 30,
              headers_provider=None):
    """发 JSON 请求；识别「HTTP 200 + 业务错误码」并按登录失效刷新登录态重试。

    headers_provider：可选无参回调，返回新的请求头（内部重新取登录态）。
    仅在识别到登录失效业务码时调用，用于刷新 cookie 后重试。
    重试耗尽仍失败 → 抛 RuntimeError（fail loud，不再静默返回空数据）。
    """
    if requests is None:
        raise RuntimeError("缺少 requests 依赖")
    last_exc = None
    last_code = None
    current_headers = headers
    refreshed = False
    for attempt in range(MAX_RETRIES):
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=current_headers, timeout=timeout, verify=False)
            else:
                resp = requests.post(url, headers=current_headers, json=payload, timeout=timeout, verify=False)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            log(f"请求失败 (尝试 {attempt + 1}/{MAX_RETRIES}) - {e}", "WARNING")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            continue
        code = _business_code(resp)
        if code is None or code in (0, "0", "Success"):
            return resp
        last_code = code
        log(f"业务错误码 code={code} (尝试 {attempt + 1}/{MAX_RETRIES})", "WARNING")
        # 登录失效且仍有重试机会时刷新登录态；最后一次刷新后无请求可用，故不重取。
        if (_is_login_expired(code) and headers_provider is not None
                and not refreshed and attempt < MAX_RETRIES - 1):
            refreshed = True
            try:
                current_headers = headers_provider()
            except Exception as e:  # noqa: BLE001
                last_exc = e
                log(f"刷新登录态失败：{e}", "WARNING")
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    if last_code is not None:
        raise RuntimeError(f"请求业务失败（登录态失效或服务异常），code={last_code}") from last_exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"请求失败：重试 {MAX_RETRIES} 次未取得有效响应")
