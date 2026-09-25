"""[B-208][S-08][E-08][E-09] 审计导出按钮与轮询/下载交互源码契约（设计 §3.3.1、§3.5；RULE-api-002）。

设计 §3.3.1「列表左主操作 · 导出」：`Button theme="solid" type="primary"`，按当前筛选创建导出任务，
提交中禁用。设计 §3.5「导出幂等约定」（RULE-api-002）：`Idempotency-Key` 由调用方在一次用户提交内
生成并复用——提交重试（网络超时/双击）复用同一 key，只有用户显式发起新导出才换 key；因此本契约钉死
「生成 key 的唯一位置是发起新提交的 `start`」，提交出口 `submit` 只透传调用方给的 key（[E-09] 的
重试入口同样不生成 key，而是经 `start` 走一次新提交）。

状态机归 `hooks/useAuditExport`：create → 有界轮询 `getExport` 至终态（丢弃乱序响应）→ `SUCCEEDED`
下载；错误码只经 catalog → i18n 映射呈现，[E-08] `IDEMPOTENCY_MISMATCH` 保留筛选且不重复建任务、
[E-09] `FAILED` 展示 `errorCode` 文案与重试入口。下载响应体是 Blob（`responseType: 'blob'`，取不到
封套 `code`），故失败文案一律来自轮询到的 `errorCode`，不得解析 Blob。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"
MODULE = SRC / "modules/audit-observability"
PAGE = MODULE / "pages/AuditPage.tsx"
BUTTON = MODULE / "components/AuditExportButton.tsx"
HOOK = MODULE / "hooks/useAuditExport.ts"
LOCALES = SRC / "locales"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 后端 snake_case 只允许出现在 service 层
BACKEND_KEYS = ("export_format", "error_code", "audit_type", "idempotency_key")

# 失败文案必须来自 catalog 错误码映射（E-08/E-09）
ERROR_CODES = ("IDEMPOTENCY_MISMATCH", "COMMON_INTERNAL_ERROR")

T_KEY = re.compile(r"t\('([^']+)'\)")


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _block(source: str, header: str) -> str:
    """取 `header` 声明起至下一个顶格 `}` 的声明体（压缩空白，便于成员断言）。"""
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header) :]
    end = body.find("\n}")
    assert end != -1, f"{header} 声明未闭合"
    return _compact(body[:end])


def _segment(source: str, start: str, end: str) -> str:
    """取 `start` 与 `end` 两个声明之间的窗口（压缩空白），用于 hook 内部各声明的职责断言。"""
    assert start in source, f"缺少声明：{start}"
    assert end in source, f"缺少声明：{end}"
    begin = source.index(start)
    finish = source.index(end, begin + len(start))
    return _compact(source[begin:finish])


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def _locale(locale: str) -> dict[str, str]:
    return json.loads(_read(LOCALES / f"{locale}.json"))


def _outside_comments(source: str, needle: str) -> int:
    return _strip_comments(source).count(needle)


def test_module_files_exist() -> None:
    """TASK-014 的两个交付文件齐备（导出按钮与状态机）。"""
    _read(BUTTON)
    _read(HOOK)


def test_export_button_is_the_left_main_action() -> None:
    """[S-08] 设计 §3.3.1：导出是工具栏左主操作位的主操作（`theme="solid" type="primary"`）。"""
    page = _read(PAGE)
    assert "from '../components/AuditExportButton'" in page, "页面须挂载 TASK-014 的导出按钮"
    assert "actions={<AuditExportButton" in _compact(page), "导出按钮须落在工具栏左主操作位"
    assert "search={<AuditFilterBar" in _compact(page), "筛选栏仍须落在工具栏右侧搜索位"
    assert "filters={" in _compact(page), "导出须携带页面当前筛选（设计 §3.5，与列表筛选同源）"

    button = _read(BUTTON)
    assert '<Button' in button and 'theme="solid"' in button and 'type="primary"' in button, (
        "导出按钮须为 primary solid（设计 §3.3.1 统一规则）"
    )
    assert 'data-testid="audit-export"' in button, "主操作须有稳定 testId"
    assert 't(\'audit.export.action\')' in button, "按钮文案须走 i18n"


def test_export_button_disables_while_submitting() -> None:
    """[S-08] 提交中禁用并展示进度：`busy` 同时驱动 disabled/loading 与进行中文案。"""
    body = _segment(_read(BUTTON), "<Button", "</Button>")
    assert "disabled={busy}" in body, "提交中须禁用（S-08：不重复提交）"
    assert "loading={busy}" in body, "提交中须展示进度"
    assert "onClick={()=>start({" in body, "点击须发起一次导出提交"
    assert "exportFormat:EXPORT_FORMAT_DEFAULT" in body, "导出格式须有显式默认值"
    assert "CSV" in _read(BUTTON), "默认导出格式取 CSV（设计 §3.3.1 未指定，交互稿只有单一导出按钮）"


def test_hook_owns_export_state_shape() -> None:
    """设计 §3.5：导出状态机归 `useAuditExport`，暴露 `{status,errorCode,busy,start,retry}`。"""
    hook = _read(HOOK)
    state = _block(hook, "export interface AuditExportState {")
    for member in (
        "status:AuditExportJob['status']|null;",
        "errorCode:string|null;",
        "busy:boolean;",
        "start(req:AuditExportCreateRequest):void;",
        "retry():void;",
    ):
        assert member in state, f"AuditExportState 缺少 {member}"

    body = _compact(hook)
    assert "return{status,errorCode,busy,start,retry};" in body, "须原样暴露约定形状"
    assert "newRequestId" in hook, "幂等键取仓库 api client 的 newRequestId"
    for banned in ("axios", "fetch(", "api.get(", "api.post("):
        assert banned not in hook, f"不得出现裸请求：{banned}"


def test_one_idempotency_key_per_submission_reused_on_retry() -> None:
    """[RULE-api-002] 一次用户提交只生成一个 key：生成点唯一，提交/重试出口只透传。"""
    hook = _read(HOOK)
    assert _outside_comments(hook, "newRequestId()") == 1, "幂等键生成点必须唯一（一次提交一个）"

    start_body = _segment(hook, "const start = useCallback(", "const retry = useCallback(")
    assert "newRequestId()" in start_body, "key 须在发起新提交时生成"
    assert start_body.index("if(busyRef.current)return;") < start_body.index("newRequestId()"), (
        "提交中重入（双击）须先返回：同一提交复用同一 key，不重复建任务"
    )
    retry_body = _segment(hook, "const retry = useCallback(", "return { status, errorCode, busy,")
    assert "start(request);" in retry_body, "[E-09] 重试入口走一次新提交"
    assert "newRequestId" not in retry_body, "重试路径不得自行生成 key（提交复用调用方持有的 key）"

    submit_body = _segment(hook, "const submit = useCallback(", "const start = useCallback(")
    assert "createExport(req,idempotencyKey)" in submit_body, "提交出口须把调用方的 key 透传给 service"
    assert "newRequestId" not in submit_body, "提交（重试）出口不得生成 key"


def test_polling_calls_get_export_until_terminal_status() -> None:
    """[S-08] 设计 §3.5：轮询 `getExport` 至终态，有界间隔且丢弃乱序响应。"""
    hook = _read(HOOK)
    assert _outside_comments(hook, "getExport(exportId)") == 1, "轮询出口须唯一"
    assert "from '../services/auditService'" in hook, "轮询只经 service 层"

    terminal = re.search(r"export const EXPORT_TERMINAL_STATUSES[^;]*;", hook)
    assert terminal, "缺少终态集合声明 EXPORT_TERMINAL_STATUSES"
    terminal_decl = terminal.group(0)
    assert "'SUCCEEDED'" in terminal_decl and "'FAILED'" in terminal_decl, (
        "终态须覆盖 SUCCEEDED/FAILED（轮询到此即停）"
    )
    assert "EXPORT_POLL_MAX_ATTEMPTS" in hook, "轮询须有次数上限（有界轮询）"
    assert "EXPORT_POLL_INTERVAL_MS" in hook, "轮询须有固定间隔"

    loop = _compact(hook)
    assert re.search(re.escape("for(letattempt=0;attempt<EXPORT_POLL_MAX_ATTEMPTS"), loop), (
        "轮询须按上限循环，不得无限请求"
    )
    assert re.search(r"constjob=awaitgetExport\(exportId\);", loop), "轮询须调用 getExport"
    assert "EXPORT_TERMINAL_STATUSES.includes(job.status)" in loop, "到达终态即停止轮询"

    # 乱序响应：每次取数前后都按 submissionSeq 判定是否仍属当前提交
    assert "constisCurrent=():boolean=>current===submissionSeq.current;" in loop
    assert loop.count("if(!isCurrent()){returnnull;}") == 2, "轮询前后都须丢弃过期提交的响应"
    assert "awaitdelay(EXPORT_POLL_INTERVAL_MS);" in loop, "间隔由 delay 承载"


def test_succeeded_triggers_download_and_failure_uses_error_code() -> None:
    """[E-09] `SUCCEEDED` 才下载；失败文案取 `errorCode`，不从 Blob 解析封套。"""
    hook = _read(HOOK)
    settle = _segment(hook, "const settle = useCallback(", "const submit = useCallback(")
    assert "downloadExport(" in settle and _outside_comments(hook, "downloadExport(") == 1, (
        "下载只在终态处理里发生一次"
    )
    assert settle.index("if(job.status!=='SUCCEEDED'){") < settle.index("downloadExport("), (
        "只有 SUCCEEDED 才触发下载（不展示未完成产物）"
    )
    assert "setErrorCode(job.errorCode??EXPORT_ERROR_FALLBACK_CODE);" in settle, (
        "[E-09] 失败文案来自轮询到的 errorCode"
    )
    assert "saveBlob(blob,exportFileName(exportId,req.exportFormat));" in settle, (
        "产物字节须交给浏览器保存"
    )

    # 下载响应体是 Blob：不得尝试解析成封套错误码
    for forbidden in ("JSON.parse", ".text()", "response.data.code"):
        assert forbidden not in hook, f"失败码不得来自 Blob 解析：{forbidden}"
    assert "apiErrorBody(error)" in _segment(
        hook, "const submit = useCallback(", "const start = useCallback("
    ), "创建失败的错误码须经 apiErrorBody 读取封套"

    saver = _compact(hook)
    assert "URL.createObjectURL(blob)" in saver and "document.createElement('a')" in saver, (
        "下载须经 Blob URL + 锚点交给浏览器（仓库暂无公共下载 helper）"
    )
    assert "anchor.download=fileName;" in saver and "URL.revokeObjectURL(url)" in saver


def test_mismatch_keeps_filters_and_creates_no_second_job() -> None:
    """[E-08] 异指纹 `IDEMPOTENCY_MISMATCH`：展示文案、保留筛选、不重复创建任务。"""
    hook = _read(HOOK)
    button = _read(BUTTON)
    assert _outside_comments(hook, "createExport(") == 1, "创建出口唯一：失败分支不得再建任务"
    assert "setQuery" not in hook and "setRequest(null)" not in hook, (
        "[E-08] 失败不改筛选（筛选由页面持有）"
    )

    failure = _compact(hook)
    assert "setErrorCode(apiErrorBody(error)?.code??EXPORT_ERROR_FALLBACK_CODE);" in failure, (
        "catalog 码须原样上抛给组件映射文案"
    )
    assert "catch(error){" in failure and "start(" not in _segment(
        hook, "} catch (error) {", "} finally {"
    ), "失败分支不得自动重试/重提（避免重复创建任务）"
    assert "onClick={retry}" in button, "[E-09] 重试入口须为显式用户操作"
    assert "useEffect" not in button, "导出不得自动提交/自动重试（[E-08] 不重复建任务）"

    keys = _compact(button)
    assert "constEXPORT_ERROR_KEYS:Record<string,string>={" in keys, "catalog 码 → i18n key 映射须显式声明"
    for code in ERROR_CODES:
        assert f"{code}:'audit.export.error.{code}'" in keys, f"缺少 {code} 的 i18n 映射"


def test_error_text_is_i18n_only_and_bilingual() -> None:
    """[RULE-i18n-001] 文案只用 i18n key；新增 key 在中英文两侧成对出现。"""
    button = _read(BUTTON)
    zh = _locale("zh-CN")
    en = _locale("en-US")
    assert set(zh) == set(en), "zh-CN/en-US 词条集合必须一致"

    keys = sorted(set(T_KEY.findall(button)))
    assert keys, "导出按钮须经 t(key) 取文案"
    for key in keys:
        assert key in zh, f"zh-CN 缺少词条 {key}"
        assert key in en, f"en-US 缺少词条 {key}"

    export_keys = [key for key in zh if key.startswith("audit.export.")]
    assert export_keys, "须新增 audit.export.* 词条（E-08/E-09 文案）"
    for key in [f"audit.export.error.{code}" for code in ERROR_CODES] + ["audit.export.errorFallback"]:
        assert key in zh and key in en, f"缺少失败文案词条 {key}"

    for path in (BUTTON, HOOK):
        offenders = [text for text in _string_literals(_read(path)) if CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"
        for backend_key in BACKEND_KEYS:
            assert backend_key not in _read(path), f"不得出现后端 snake_case 字段 {backend_key}"
