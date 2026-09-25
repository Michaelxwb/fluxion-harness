"""[RULE-front-001] 运行审计前端 service 层与类型契约（设计 §3.4/§3.5）。

前端 API 只经 services/ 收口（共享 apiClient 自动带 X-Locale/X-Request-Id/CSRF），组件不裸用
axios/fetch；service/types 只表达结构，文案由组件用 i18n key 承载；后端 snake_case 与前端
camelCase 的字段映射只发生在 service 层（`actor_user_id` → `actorUserId`、`page_size` → `pageSize`）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/audit-observability"
SERVICE = MODULE / "services/auditService.ts"
TYPES = MODULE / "types.ts"

SERVICE_METHODS = ("listAudits", "getAudit", "createExport", "getExport", "downloadExport")

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 后端 PROJECTED_COLUMNS（snake_case）→ 前端 camelCase
FIELD_MAPPING = (
    ("auditId", "audit_id"),
    ("auditType", "audit_type"),
    ("resourceType", "resource_type"),
    ("resourceId", "resource_id"),
    ("actorUserId", "actor_user_id"),
    ("actorName", "actor_name"),
    ("agentId", "agent_id"),
    ("agentName", "agent_name"),
    ("resultStatus", "result_status"),
    ("traceId", "trace_id"),
    ("occurredAt", "occurred_at"),
    ("latencyMs", "latency_ms"),
    ("pageSize", "page_size"),
)


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _block(source: str, header: str) -> str:
    """取 `header` 声明起至下一个顶格 `}` 的声明体（压缩空白，便于字段断言）。"""
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header) :]
    end = body.find("\n}")
    assert end != -1, f"{header} 声明未闭合"
    return _compact(body[:end])


def _function(source: str, name: str) -> str:
    """取 `export async function <name>(...)` 的实现（到下一个顶层 export 声明为止）。"""
    marker = f"export async function {name}("
    assert marker in source, f"缺少 service 方法：{name}"
    body = source[source.index(marker) :]
    following = body.find("\nexport ", 1)
    return body if following == -1 else body[:following]


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def test_module_files_exist() -> None:
    """模块目录与两个契约文件齐备。"""
    _source(SERVICE)
    _source(TYPES)


def test_service_uses_shared_api_client_only() -> None:
    """[RULE-front-001] 只经共享 apiClient；不裸用 axios/fetch，也不自建实例。"""
    source = _source(SERVICE)
    assert "from '../../../api/client'" in source
    assert "api.get" in source
    assert "api.post" in source
    assert "axios" not in source
    assert "fetch(" not in source
    assert "create(" not in source


def test_service_declares_five_methods_with_documented_params() -> None:
    """设计 §3.5：五个 service 方法及其参数形状。"""
    source = _source(SERVICE)
    for method in SERVICE_METHODS:
        assert f"export async function {method}(" in source, method

    list_body = _compact(_function(source, "listAudits"))
    assert "params:AuditListQuery" in list_body
    assert "Promise<AuditPage>" in list_body

    detail_body = _compact(_function(source, "getAudit"))
    assert "auditType:AuditListItem['auditType']" in detail_body
    assert "id:string" in detail_body
    assert "Promise<AuditDetail>" in detail_body

    create_body = _compact(_function(source, "createExport"))
    assert "req:AuditExportCreateRequest" in create_body
    assert "idempotencyKey:string" in create_body
    assert "Promise<AuditExportJob>" in create_body

    assert "exportId:string" in _compact(_function(source, "getExport"))
    assert "exportId:string" in _compact(_function(source, "downloadExport"))


def test_service_paths_match_backend_contract() -> None:
    """设计 §3.4/§3.5 的五个后端路径；详情必须同时传 `audit_type`（4 张来源表 UUID 不互通）。"""
    source = _source(SERVICE)
    assert "('/audits',{params:" in _compact(_function(source, "listAudits"))
    assert "/audits/${id}" in _function(source, "getAudit")
    assert "audit_type:auditType" in _compact(_function(source, "getAudit"))
    assert "'/audits/exports'" in _function(source, "createExport")
    assert "/audits/exports/${exportId}`" in _function(source, "getExport")
    assert "/audits/exports/${exportId}/download" in _function(source, "downloadExport")
    assert source.count("/audits/exports/${exportId}") == 2


def test_query_page_size_is_clamped_to_100() -> None:
    """设计 §3.4：`pageSize <= 100`，默认 20（默认值由页面初始筛选持有）。"""
    types = _source(TYPES)
    assert "AUDIT_PAGE_SIZE_MAX=100" in _compact(types)
    assert "AUDIT_PAGE_SIZE_DEFAULT=20" in _compact(types)
    assert "Math.min(" in _source(SERVICE)
    assert "AUDIT_PAGE_SIZE_MAX" in _source(SERVICE)


def test_create_export_carries_caller_owned_idempotency_key() -> None:
    """[RULE-api-002] `Idempotency-Key` 由调用方传给 service，并以请求头形式发送。

    设计 §3.5 导出幂等约定：key 在一次用户提交内生成并复用（提交重试/双击必须复用同一 key），
    只有用户显式发起新导出才换 key；因此 service 不得在调用时自行 newRequestId()。
    """
    create_body = _compact(_function(_source(SERVICE), "createExport"))
    assert "'Idempotency-Key':idempotencyKey" in create_body
    assert "newRequestId" not in create_body


def test_idempotency_key_reuse_convention_is_documented() -> None:
    """幂等约定必须在 service 内以注释写明归属与复用语义（设计 §3.5）。"""
    comments = "\n".join(re.findall(r"^\s*(?://|\*|/\*).*$", _source(SERVICE), re.MULTILINE))
    assert re.search(r"复用|reus", comments, re.IGNORECASE), "须写明重试复用同一 Idempotency-Key"
    assert re.search(r"调用方|caller", comments, re.IGNORECASE), "须写明 key 由调用方持有"


def test_service_maps_snake_case_to_camel_case() -> None:
    """设计 §3.5：后端 snake_case ↔ 前端 camelCase 的映射只在本层发生。"""
    source = _source(SERVICE)
    for camel, snake in FIELD_MAPPING:
        assert re.search(rf"{camel}:\s*[\w.()]*{snake}\b", source), f"缺少 {snake} → {camel} 映射"
    # 查询参数出参使用后端 snake_case 命名
    assert re.search(r"page_size:\s*[\w.()]*pageSize", source)
    assert "audit_type:" in source and "result_status:" in source


def test_export_filters_reuse_list_query_shape() -> None:
    """导出筛选与列表筛选同源：`Omit<AuditListQuery, 'page' | 'pageSize'>`。"""
    body = _block(_source(TYPES), "export interface AuditExportCreateRequest {")
    assert "filters:Omit<AuditListQuery,'page'|'pageSize'>;" in body


def test_types_declare_camel_case_contract() -> None:
    """设计 §3.4 四类类型及其 camelCase 字段。"""
    source = _source(TYPES)

    query = _block(source, "export interface AuditListQuery {")
    for field in (
        "auditType?:'CONFIG'|'TOOL'|'EGRESS'|'MODEL';",
        "resourceType?:string;",
        "resourceId?:string;",
        "actorUserId?:string;",
        "action?:string;",
        "resultStatus?:string;",
        "traceId?:string;",
        "startTime?:string;",
        "endTime?:string;",
        "page:number;",
        "pageSize:number;",
    ):
        assert field in query, field
    assert "audit_type" not in query and "page_size" not in query

    item = _block(source, "export interface AuditListItem {")
    for field in (
        "auditId:string;",
        "auditType:'CONFIG'|'TOOL'|'EGRESS'|'MODEL';",
        "resourceType:string;",
        "resourceId:string;",
        "actorUserId:string;",
        "actorName?:string;",
        "agentId?:string;",
        "agentName?:string;",
        "action:string;",
        "resultStatus:string;",
        "traceId?:string;",
        "occurredAt:string;",
        "target:string;",
        "latencyMs?:number;",
    ):
        assert field in item, field

    request = _block(source, "export interface AuditExportCreateRequest {")
    assert "exportFormat:'CSV'|'JSON';" in request

    job = _block(source, "export interface AuditExportJob {")
    for field in (
        "exportId:string;",
        "status:'PENDING'|'RUNNING'|'SUCCEEDED'|'FAILED';",
        "rowCount?:number;",
        "errorCode?:string;",
        "createTime:string;",
        "updateTime:string;",
    ):
        assert field in job, field


def test_no_hardcoded_user_visible_chinese_in_service_and_types() -> None:
    """[RULE-front-001] service/types 不承载 UI 文案：字符串字面量不得含中文/全角字符。"""
    for path in (SERVICE, TYPES):
        offenders = [text for text in _string_literals(_source(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"
