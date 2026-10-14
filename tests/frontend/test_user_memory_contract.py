"""[RULE-front-001][E-04] 记忆服务层与严格 DTO 源码契约（design §3.7 API 与 Console）。

Console 记忆 HTTP 只在 `modules/user-identity/services/users.ts` 收口（共享 axios 实例）；
组件不裸用 axios/fetch，也不自行拆错误体。`memoryTypes.ts` 承载与后端 DTO 对齐的严格类型：
列表项/历史项/写操作入参/Runtime 命令回执，以及 status/author/category/命令结果枚举 ——
无 `any`、无旧 `source_type` 适配。404/409/403 的错误语义只经 `apiErrorBody`（`memoryErrorCode`）
映射，服务层不重复解析 AxiosError 形状。

RED 语义：本文件先于实现落地，上述契约缺失（缺文件/缺出口/缺签名/旧字段残留）即失败。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/user-identity"
SERVICE = MODULE / "services/users.ts"
TYPES = MODULE / "memoryTypes.ts"
COMPONENT = MODULE / "UserDetailTabs.tsx"
MEMORY_TAB = MODULE / "MemoryTab.tsx"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _decl(source: str, marker: str) -> str:
    """取 `marker` 起至下一个顶层 export 声明（压缩空白，便于逐字段断言）。"""
    assert marker in source, f"缺少声明：{marker}"
    body = source[source.index(marker):]
    following = body.find("\nexport ", 1)
    return _compact(body if following == -1 else body[:following])


def _block(source: str, header: str) -> str:
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header):]
    end = body.find("\n}")
    assert end != -1, f"{header} 声明未闭合"
    return _compact(body[:end])


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def test_memory_types_declares_strict_enums() -> None:
    """status/author/category 与 Runtime 命令结果用字面量联合，而不是 string/any。"""
    types = _compact(_source(TYPES))
    assert "exporttypeMemoryStatus='ACTIVE'|'DISABLED'|'EXPIRED';" in types
    assert "exporttypeMemoryAuthor='USER'|'ADMIN'|'AGENT';" in types
    assert "exporttypeMemoryCategory='PREFERENCE'|'WORK_STYLE'|'EXPLICIT'|'TASK_STATE';" in types
    assert "exporttypeMemoryWritableCategory='PREFERENCE'|'WORK_STYLE'|'EXPLICIT';" in types
    assert "exporttypeMemoryCommandStatus='APPLIED'|'UNCHANGED'|'STALE';" in types
    assert (
        "exporttypeMemoryResultStatus=|'CREATED'|'UPDATED'|'DELETED'|'ENABLED'|'DISABLED'"
        "|'RESTORED'|'RETIRED'|'UNCHANGED'|'SKIPPED';" in types
    )


def test_memory_types_forbid_any_and_legacy_source_type() -> None:
    """[RULE-front-001] 类型不放任 any，也不做旧 source_type 适配。"""
    code = _strip_comments(_source(TYPES))
    assert "source_type" not in code
    assert not re.search(r"(?<![\w.])any(?![\w])", code), "memoryTypes 不得出现 any"


def test_memory_item_matches_backend_projection() -> None:
    """Console `MemoryItem`（TASK-024 读投影）逐字段对齐。"""
    item = _block(_source(TYPES), "export interface MemoryItem {")
    for field in (
        "id:string;",
        "memory_key:string;",
        "category:MemoryCategory;",
        "content:MemoryContent;",
        "author:MemoryAuthor;",
        "version:number;",
        "enabled:boolean;",
        "explicit_request:boolean;",
        "confirmed_count:number;",
        "status:MemoryStatus;",
        "source_ref:string|null;",
        "source_quote:string|null;",
        "expires_at:string|null;",
        "last_confirmed_at:string|null;",
        "create_time:string;",
        "update_time:string;",
    ):
        assert field in item, f"MemoryItem 缺少字段：{field}"
    assert "source_type" not in item


def test_memory_history_item_matches_backend_projection() -> None:
    """Console `MemoryHistoryItem`：author 与 actor 分列，`after` 是历史快照。"""
    item = _block(_source(TYPES), "export interface MemoryHistoryItem {")
    for field in (
        "id:string;",
        "origin_id:string;",
        "memory_key:string;",
        "version:number;",
        "change_reason:string;",
        "actor_type:string;",
        "actor_id:string|null;",
        "command_id:string;",
        "source_run_id:string|null;",
        "after:Record<string,unknown>;",
        "create_time:string;",
    ):
        assert field in item, f"MemoryHistoryItem 缺少字段：{field}"


def test_memory_receipt_matches_runtime_command_response() -> None:
    """写路由返回 Runtime 命令回执（`MemoryCommandResponse`）逐字段对齐。"""
    types = _source(TYPES)
    result = _block(types, "export interface MemoryMutationResult {")
    for field in (
        "memory_key:string;",
        "id:string|null;",
        "version:number|null;",
        "status:MemoryResultStatus;",
        "reason:string|null;",
    ):
        assert field in result, f"MemoryMutationResult 缺少字段：{field}"
    receipt = _block(types, "export interface MemoryCommandReceipt {")
    for field in (
        "command_id:string;",
        "scope_revision:number;",
        "forget_epoch:number;",
        "changed_count:number;",
        "status:MemoryCommandStatus;",
        "items:MemoryMutationResult[];",
    ):
        assert field in receipt, f"MemoryCommandReceipt 缺少字段：{field}"


def test_memory_write_inputs_align_console_admin_dto() -> None:
    """写操作入参：管理员不得写 TASK_STATE；覆盖/启禁用都显式带读到的版本。"""
    types = _source(TYPES)
    create = _block(types, "export interface MemoryCreateInput {")
    for field in (
        "memory_key:string;",
        "category:MemoryWritableCategory;",
        "value:string;",
        "expected_version?:number;",
        "expires_at?:string;",
    ):
        assert field in create, f"MemoryCreateInput 缺少字段：{field}"
    enabled = _block(types, "export interface MemoryEnabledUpdateInput {")
    for field in ("enabled:boolean;", "expected_version:number;", "expires_at?:string;"):
        assert field in enabled, f"MemoryEnabledUpdateInput 缺少字段：{field}"
    assert "TASK_STATE" not in _compact(_decl(types, "export type MemoryWritableCategory"))


def test_memory_list_query_uses_backend_filter_names() -> None:
    """GET 列表过滤：category/author/status 与后端 query 同名；分页由 PageParams 承载。"""
    query = _block(_source(TYPES), "export interface MemoryListQuery {")
    for field in ("category?:MemoryCategory;", "author?:MemoryAuthor;", "status?:MemoryStatus;"):
        assert field in query, f"MemoryListQuery 缺少字段：{field}"
    assert "page_size" not in query


def test_memory_error_codes_cover_404_409_403_semantics() -> None:
    """错误码枚举与后端错误目录同名；404/409/403 语义由 `memoryErrorCode` 收口。"""
    types = _source(TYPES)
    codes = _compact(_decl(types, "export const MEMORY_ERROR_CODES"))
    for code in (
        "MEMORY_NOT_FOUND",
        "COMMON_NOT_FOUND",
        "MEMORY_VERSION_CONFLICT",
        "IDEMPOTENCY_MISMATCH",
        "FORBIDDEN",
        "MEMORY_VALUE_INVALID",
        "COMMON_VALIDATION_ERROR",
        "COMMON_INTERNAL_ERROR",
    ):
        assert code in codes, f"MEMORY_ERROR_CODES 缺少：{code}"
    assert "exporttypeMemoryErrorCode=(typeofMEMORY_ERROR_CODES)[number];" in _compact(types)


def test_service_imports_shared_types_and_error_helper() -> None:
    """[RULE-front-001] service 只 import 共享 axios 与 memoryTypes，不自建实例/重复类型。"""
    source = _compact(_source(SERVICE))
    assert "from'../../../api/client'" in source
    assert "from'../memoryTypes'" in source
    assert "interface Memory {" not in source
    assert "source_type" not in source
    assert "axios" not in source
    assert "fetch(" not in source
    assert "create(" not in source


def test_list_memory_signature_and_filters() -> None:
    body = _decl(_source(SERVICE), "export async function listMemory(")
    assert "listMemory(id:string,params:PageParams&MemoryListQuery):Promise<Page<MemoryItem>>" in body


def test_list_memory_history_signature() -> None:
    body = _decl(_source(SERVICE), "export async function listMemoryHistory(")
    assert (
        "listMemoryHistory(id:string,memoryKey:string,params:PageParams)"
        ":Promise<Page<MemoryHistoryItem>>" in body
    )


def test_write_functions_require_caller_owned_idempotency_key() -> None:
    """写操作都收调用方持有的 `Idempotency-Key`（重试复用），service 不自行生成。"""
    source = _source(SERVICE)
    signatures = {
        "createMemory": "(id:string,input:MemoryCreateInput,idempotencyKey:string)",
        "updateMemoryEnabled": (
            "(id:string,memoryKey:string,input:MemoryEnabledUpdateInput,idempotencyKey:string)"
        ),
        "deleteMemory": "(id:string,memoryId:string,expectedVersion:number,idempotencyKey:string)",
        "clearMemory": "(id:string,idempotencyKey:string)",
    }
    for name, signature in signatures.items():
        body = _decl(source, f"export async function {name}(")
        assert f"{name}{signature}:Promise<MemoryCommandReceipt>" in body, f"{name} 签名不符"
        assert "'Idempotency-Key':idempotencyKey" in body, f"{name} 未发送 Idempotency-Key"
        assert "newRequestId" not in body, f"{name} 不得自行生成幂等键（由调用方持有）"


def test_service_paths_and_verbs_match_backend_contract() -> None:
    """后端四条路由：GET 列表/历史、POST、PATCH、DELETE（按 id）/DELETE（清空）。"""
    source = _source(SERVICE)
    list_body = _decl(source, "export async function listMemory(")
    assert "api.get<ApiResponse<Page<MemoryItem>>>(`/users/${id}/memory`,{params})" in list_body
    history_body = _decl(source, "export async function listMemoryHistory(")
    assert (
        "api.get<ApiResponse<Page<MemoryHistoryItem>>>(`/users/${id}/memory/${memoryKey}/history`,"
        "{params})" in history_body
    )
    create_body = _decl(source, "export async function createMemory(")
    assert "api.post<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory`,input,{" in create_body
    update_body = _decl(source, "export async function updateMemoryEnabled(")
    assert (
        "api.patch<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory/${memoryKey}`,"
        "input,{" in update_body
    )
    delete_body = _decl(source, "export async function deleteMemory(")
    assert "api.delete<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory/${memoryId}`,{" in delete_body
    assert "params:{expected_version:expectedVersion}" in delete_body
    clear_body = _decl(source, "export async function clearMemory(")
    assert "api.delete<ApiResponse<MemoryCommandReceipt>>(`/users/${id}/memory`,{" in clear_body


def test_error_semantics_only_through_api_error_body() -> None:
    """[RULE-front-001] `memoryErrorCode` 只经 `apiErrorBody`，不自行拆 AxiosError 形状。"""
    helper = _decl(_source(SERVICE), "export function memoryErrorCode(")
    assert "apiErrorBody(error)" in helper
    assert "response" not in helper, "错误体解析只经 apiErrorBody"


def test_memory_tab_renders_author_through_memory_options() -> None:
    """组件适配收口：来源列读 author 并经 memoryOptions 本地化；旧 sourceType 词条引用清零。"""
    source = _source(MEMORY_TAB)
    assert "source_type" not in source
    assert "dataIndex: 'author'" in source
    assert "memoryAuthorLabel" in source
    assert "user.memory.sourceType." not in source
    # 记忆 Tab 已拆出：UserDetailTabs 不再自绘记忆表，也不保留旧来源格式化
    detail = _source(COMPONENT)
    assert "formatMemoryAuthor" not in detail
    assert "user.memory.sourceType." not in detail


def test_memory_tab_commands_carry_version_and_caller_key() -> None:
    """删除/清空传入读到的 version 与调用方幂等键；HTTP 仍在 service 层。"""
    compact = _compact(_source(MEMORY_TAB))
    assert "deleteMemory(props.userId,entry.id,entry.version,newRequestId())" in compact
    assert "clearMemory(props.userId,newRequestId())" in compact
    assert "from'../../api/client'" in compact


def test_service_and_types_carry_no_hardcoded_ui_text() -> None:
    """[RULE-front-001] service/types 不承载 UI 文案：字符串字面量不得含中文/全角字符。"""
    for path in (SERVICE, TYPES):
        offenders = [text for text in _string_literals(_source(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"
