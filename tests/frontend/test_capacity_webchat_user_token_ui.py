"""[B-285] 用户详情 Webchat Token 凭据卡片（design frontend §3.2/§3.4/§3.7；backend §3.4 API-03）。

真实边界：真实模块 service（`modules/user-identity/services/users.ts` 的 API-03 出口）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器）、仓库内 esbuild 编译产物与真实 node 执行；纯解析/状态
（元信息 DTO、CAS 版本派生、错误码映射、原文纪律）跑真实代码，DOM 交互留真实浏览器 E2E
（S-114/S-115，TASK-115）。断言覆盖：

- **仅 ADMIN 可见**：卡片自身 `return null`，且挂载点在用户详情的 IM 身份 Tab（同门控）；
- **GET 只元信息**：响应恰好六字段（`configured/status/token_id/revision/issued_at/expires_at`），
  未知/外租户用户读作 `NOT_CONFIGURED`（`configured=false`、`revision=0`）⇒ 首签用版本 0；
- **原文全响应恰一次**：`PUT` 响应是唯一携带 `token` 的地方，不进 URL/参数/读取面/后续请求体；
- **版本 CAS**：生成/重置 `PUT {expected_revision}`（0=首签、N=最新签发行），撤销
  `DELETE ?expected_revision=N`（版本幂等，不误伤新版本）；
- **重置/撤销走 ConfirmAction**：两者都明示关联登录会话将失效，且生成/重置/撤销有单飞守卫；
- **响应未知不自动轮换**：失败/冲突只重读元信息（GET），service 不重试、卡片不自动重签；
- **原文只在本次结果框**：`CopyButton` 复制、关闭即清、不落持久缓存/URL/日志、GET 不回明文；
- **词条齐平**：`user.webchatToken.*` zh-CN/en-US 两侧齐平，状态词条不是裸枚举码。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/user-identity"
SERVICE_TS = MODULE / "services/users.ts"
CARD_TSX = MODULE / "UserChatTokenCard.tsx"
TABS_TSX = MODULE / "UserDetailTabs.tsx"
CLIENT_TS = SRC / "api/client.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CANARY = "mwt-canary-085"
RESET_CANARY = "mwt-reset-canary-085"
USER_ID = "44444444-4444-4444-8444-444444444444"
TOKEN_ID = "55555555-5555-4555-8555-555555555555"
ISSUED_AT = "2026-10-18T00:00:00Z"
EXPIRES_AT = "2026-11-17T00:00:00Z"

#: 新词条：Token 凭据卡片的全部文案（两侧必须齐平，见 test_token_keys_are_localized）。
REQUIRED_KEYS = (
    "user.webchatToken.title",
    "user.webchatToken.subtitle",
    "user.webchatToken.statusLabel",
    "user.webchatToken.revision",
    "user.webchatToken.issuedAt",
    "user.webchatToken.expiresAt",
    "user.webchatToken.status.NOT_CONFIGURED",
    "user.webchatToken.status.ACTIVE",
    "user.webchatToken.status.EXPIRED",
    "user.webchatToken.status.REVOKED",
    "user.webchatToken.issue",
    "user.webchatToken.reset",
    "user.webchatToken.revoke",
    "user.webchatToken.confirmReset",
    "user.webchatToken.confirmRevoke",
    "user.webchatToken.notConfigured",
    "user.webchatToken.onceHint",
    "user.webchatToken.copy",
    "user.webchatToken.closeResult",
    "user.webchatToken.hint",
    "user.webchatToken.error.revisionConflict",
    "user.webchatToken.error.notFound",
    "user.webchatToken.error.forbidden",
)

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Button = () => null;
export const Tooltip = () => null;
"""

ENTRY = """export * from '{service}';
export {{ api }} from '{client}';
"""

HARNESS_JS = """
const toasts = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_csrf=console-csrf-value' };
globalThis.window = { location: { pathname: '/users', search: '' } };

const CANARY = '__CANARY__';
const RESET_CANARY = '__RESET_CANARY__';
const USER_ID = '__USER_ID__';
const TOKEN_ID = '__TOKEN_ID__';
const ISSUED_AT = '__ISSUED_AT__';
const EXPIRES_AT = '__EXPIRES_AT__';

const mod = await import('./users.mjs');
const { api, ...service } = mod;

const requests = [];
let payload = null;
let failNext = false;

const headerMap = (config) => {
  const headers = config.headers;
  if (headers && typeof headers.toJSON === 'function') return headers.toJSON();
  return { ...headers };
};

api.defaults.adapter = async (config) => {
  const entry = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    headers: headerMap(config),
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : null
  };
  if (failNext) {
    failNext = false;
    entry.response = { code: 'REVISION_CONFLICT', msg: '版本冲突' };
    requests.push(entry);
    return { data: entry.response, status: 200, statusText: 'OK', headers: {}, config };
  }
  entry.response = payload;
  requests.push(entry);
  return {
    data: {
      code: '0',
      msg: 'ok',
      data: payload,
      trace_id: 'trace',
      request_id: 'req',
      timestamp: '2026-10-18T00:00:00Z'
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const observed = () => {
  const entry = requests.at(-1);
  return {
    method: entry.method,
    url: entry.url,
    params: entry.params,
    body: entry.body,
    requestId: entry.headers['X-Request-Id'] ?? null,
    csrf: entry.headers['X-CSRF-Token'] ?? null
  };
};

const captureAsync = async (run) => {
  try {
    await run();
    return { ok: true };
  } catch (error) {
    return { ok: false, error };
  }
};

const metadata = (overrides) => ({
  configured: true,
  status: 'ACTIVE',
  token_id: TOKEN_ID,
  revision: 1,
  issued_at: ISSUED_AT,
  expires_at: EXPIRES_AT,
  ...overrides
});

const observations = {};

// 1) GET 元信息：未签发/外租户用户读作 NOT_CONFIGURED（六字段全空、revision=0）
payload = {
  configured: false,
  status: 'NOT_CONFIGURED',
  token_id: null,
  revision: 0,
  issued_at: null,
  expires_at: null
};
observations.metadata = await service.getWebchatToken(USER_ID);
observations.metadataRequest = observed();
observations.metadataKeys = Object.keys(observations.metadata).sort();
observations.firstIssueRevision = service.webchatTokenExpectedRevision(observations.metadata);
// 未配置一律按首签处理：即使快照带着非零 revision 也不能当成重置基准；
// 已配置则必须用最新签发行版本（含已撤销/已过期版本，撤销不删行）。
observations.unconfiguredRevision = service.webchatTokenExpectedRevision({
  configured: false,
  status: 'NOT_CONFIGURED',
  token_id: null,
  revision: 7,
  issued_at: null,
  expires_at: null
});
observations.configuredRevision = service.webchatTokenExpectedRevision(
  metadata({ revision: 3 })
);

// 2) 首签：expected_revision=0，原文只在这条响应里
payload = metadata({ token: CANARY });
observations.issueResult = await service.issueWebchatToken(
  USER_ID,
  observations.firstIssueRevision
);
observations.issueRequest = observed();
observations.issueKeys = Object.keys(observations.issueResult).sort();

// 3) 重置：按最新签发行版本（含已撤销/已过期版本，撤销不删行）
payload = metadata({ revision: 2, token: RESET_CANARY });
observations.resetRevision = service.webchatTokenExpectedRevision(observations.issueResult);
observations.resetResult = await service.issueWebchatToken(USER_ID, observations.resetRevision);
observations.resetRequest = observed();

// 4) 撤销：DELETE ?expected_revision=N（版本幂等，不误伤后续新版本）
payload = metadata({ status: 'REVOKED', revision: 2 });
observations.revokeResult = await service.revokeWebchatToken(USER_ID, 2);
observations.revokeRequest = observed();

// 5) 冲突/响应不明：service 不自动重试（调用方只允许重读元信息，不自动轮换）
failNext = true;
const before = requests.length;
const conflict = await captureAsync(() => service.issueWebchatToken(USER_ID, 2));
observations.conflict = {
  failed: !conflict.ok,
  errorKey: conflict.ok ? null : service.webchatTokenErrorKey(conflict.error),
  requestCount: requests.length - before
};

// 6) 原文纪律：只进写入体；只有签发/重置的响应各携带一次
const withCanary = (value) => JSON.stringify(value ?? null).includes(CANARY);
const withResetCanary = (value) => JSON.stringify(value ?? null).includes(RESET_CANARY);
observations.canaryInUrl = requests
  .filter((entry) => `${entry.url}${JSON.stringify(entry.params ?? null)}`.includes(CANARY))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.canaryInBody = requests
  .filter((entry) => withCanary(entry.body))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.canaryInResponses = requests
  .filter((entry) => withCanary(entry.response))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.resetCanaryInResponses = requests
  .filter((entry) => withResetCanary(entry.response))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.metadataCarriesToken = 'token' in observations.metadata;

// 7) 错误码 → 本地化文案键
observations.errors = {
  conflict: service.webchatTokenErrorKey({
    response: { data: { code: 'REVISION_CONFLICT', msg: '版本冲突' } }
  }),
  notFound: service.webchatTokenErrorKey({
    response: { data: { code: 'COMMON_NOT_FOUND', msg: '不存在' } }
  }),
  forbidden: service.webchatTokenErrorKey({ code: 'FORBIDDEN' }),
  unknown: service.webchatTokenErrorKey(new Error('boom'))
};

console.log(JSON.stringify(observations));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_service_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包真实 service（含共享传输）并驱动真实 axios 拦截器。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(service=SERVICE_TS, client=CLIENT_TS), encoding="utf-8"
    )
    harness = (
        HARNESS_JS.replace("__CANARY__", CANARY)
        .replace("__RESET_CANARY__", RESET_CANARY)
        .replace("__USER_ID__", USER_ID)
        .replace("__TOKEN_ID__", TOKEN_ID)
        .replace("__ISSUED_AT__", ISSUED_AT)
        .replace("__EXPIRES_AT__", EXPIRES_AT)
    )
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'users.mjs'}",
            f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"

    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def token_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("user-token-ui"))


def test_real_service_reads_metadata_only(token_service: dict[str, Any]) -> None:
    """API-03 GET：恰好六字段元信息；未知/外租户用户读作 NOT_CONFIGURED ⇒ 首签版本 0。"""
    request = token_service["metadataRequest"]
    assert request["method"] == "GET"
    assert request["url"] == f"/users/{USER_ID}/webchat-token"
    assert request["params"] is None
    assert request["body"] is None

    assert token_service["metadataKeys"] == [
        "configured",
        "expires_at",
        "issued_at",
        "revision",
        "status",
        "token_id",
    ], "读取面只能是契约的六字段：不夹带原文，也不夹带摘要"
    assert token_service["metadata"]["status"] == "NOT_CONFIGURED"
    assert token_service["metadata"]["configured"] is False
    assert token_service["metadata"]["revision"] == 0
    assert token_service["metadataCarriesToken"] is False, "GET 永不回明文"
    assert token_service["firstIssueRevision"] == 0, (
        "未配置（含未知/外租户用户）首签必须用 expected_revision=0"
    )
    assert token_service["unconfiguredRevision"] == 0, (
        "首签判定只看 configured：未配置时任何快照版本都不能被当成重置基准"
    )
    assert token_service["configuredRevision"] == 3, (
        "已配置时按最新签发行版本 CAS（撤销不删行，版本继续递增）"
    )


def test_real_service_issues_and_resets_with_version_cas(token_service: dict[str, Any]) -> None:
    """签发/重置：`PUT {expected_revision}`，0=首签、N=最新签发行，版本单胜由后端裁决。"""
    issued = token_service["issueRequest"]
    assert issued["method"] == "PUT"
    assert issued["url"] == f"/users/{USER_ID}/webchat-token"
    assert issued["body"] == {"expected_revision": 0}, "首签体只能是 {expected_revision: 0}"
    assert issued["csrf"] == "console-csrf-value", "凭据写操作必须带共享传输的 CSRF 头"
    assert issued["requestId"], "写操作仍走共享传输的 X-Request-Id"
    assert token_service["issueResult"]["token"] == CANARY
    assert token_service["issueResult"]["status"] == "ACTIVE"
    assert token_service["issueResult"]["revision"] == 1
    assert set(token_service["issueKeys"]) - set(token_service["metadataKeys"]) == {"token"}, (
        "签发响应 = 六字段元信息 + 一次性原文，不多不少"
    )

    reset = token_service["resetRequest"]
    assert reset["method"] == "PUT"
    assert reset["body"] == {"expected_revision": 1}, (
        "重置必须按元信息里的最新签发行版本 CAS（撤销不删行，版本继续递增）"
    )
    assert token_service["resetRevision"] == 1
    assert token_service["resetResult"]["revision"] == 2


def test_real_service_revokes_the_named_version(token_service: dict[str, Any]) -> None:
    """撤销：`DELETE ?expected_revision=N` 只作用于指定版本，重复/迟到版本由后端幂等裁决。"""
    request = token_service["revokeRequest"]
    assert request["method"] == "DELETE"
    assert request["url"] == f"/users/{USER_ID}/webchat-token"
    assert request["params"] == {"expected_revision": 2}
    assert request["body"] is None
    assert request["csrf"] == "console-csrf-value"
    assert token_service["revokeResult"]["status"] == "REVOKED"
    assert token_service["revokeResult"]["revision"] == 2


def test_real_service_keeps_the_plaintext_to_one_response(token_service: dict[str, Any]) -> None:
    """原文只在签发/重置响应各出现一次：绝不回传服务器、不进 URL/参数/读取面。"""
    assert token_service["canaryInUrl"] == [], "原文不得进入 URL 或查询参数"
    assert token_service["canaryInBody"] == [], (
        "原文绝不回传服务器：没有任何请求体（含重放/重试）携带它"
    )
    assert token_service["canaryInResponses"] == [f"PUT /users/{USER_ID}/webchat-token"], (
        "原文全响应恰一次：除了本次签发响应，任何读取/撤销响应都不得携带它"
    )
    assert token_service["resetCanaryInResponses"] == [f"PUT /users/{USER_ID}/webchat-token"]


def test_real_service_never_rotates_on_unknown_outcome(token_service: dict[str, Any]) -> None:
    """冲突/响应不明：service 只如实失败一次，不自动重试、不自动轮换，错误码交调用方映射。"""
    conflict = token_service["conflict"]
    assert conflict["failed"] is True
    assert conflict["requestCount"] == 1, "版本冲突后不得自动重发签发请求（响应丢失不自动轮换）"
    assert conflict["errorKey"] == "user.webchatToken.error.revisionConflict"
    assert token_service["errors"] == {
        "conflict": "user.webchatToken.error.revisionConflict",
        "notFound": "user.webchatToken.error.notFound",
        "forbidden": "user.webchatToken.error.forbidden",
        "unknown": "common.saveFailed",
    }


def test_card_is_admin_gated_and_reveals_the_plaintext_once() -> None:
    """[RULE-front-001] 卡片仅 ADMIN；原文只在本次结果框展示/复制，关闭即清，GET 回不来。"""
    source = _read(CARD_TSX)
    assert "useAuth" in source
    assert "account?.role === 'ADMIN'" in source
    assert re.search(r"if\(!isAdmin\)\{returnnull;?\}", _compact(source)), (
        "非 ADMIN 必须直接不渲染卡片（不是只跳过请求）"
    )
    assert 'data-testid="webchat-token-issued"' in source, "本次签发结果必须有独立结果框"
    assert "<CopyButton value={props.issued.token}" in source, "原文只能经公共 CopyButton 复制"
    assert "setIssued(null)" in source, "关闭结果框必须清空原文"
    assert "user.webchatToken.onceHint" in source, "必须明示原文只展示一次"
    for leaked in ("localStorage", "sessionStorage", "console.log", "history.pushState", "location.href"):
        assert leaked not in source, f"原文不得进入持久缓存/URL/日志：{leaked}"
    assert "getWebchatToken(" not in source, "卡片不自行读取元信息，更不得从 GET 恢复原文"


def test_card_uses_confirm_action_and_versioned_mutations() -> None:
    """生成/重置/撤销都携版本；重置与撤销走 ConfirmAction 并明示登录会话失效；单飞守卫。"""
    source = _read(CARD_TSX)
    assert "ConfirmAction" in source
    assert "user.webchatToken.confirmReset" in source and "user.webchatToken.confirmRevoke" in source
    assert re.search(r"confirm(Reset|Revoke)': '.*会话", source) or "会话" in _read(
        LOCALES / "zh-CN.json"
    ), "重置/撤销必须明示关联登录会话将失效（本地化文案）"
    assert "webchatTokenExpectedRevision(props.metadata)" in source, "CAS 版本只能从元信息派生"
    assert "issueWebchatToken(props.userId, expectedRevision)" in source
    assert "revokeWebchatToken(props.userId, expectedRevision)" in source
    assert "inFlight.current" in source, "生成/重置/撤销必须有单飞守卫"
    assert "disabled={pending}" in source, "生成按钮必须按在飞状态禁用"


def test_card_refreshes_metadata_only_when_the_outcome_is_unknown() -> None:
    """响应未知只重读元信息：签发/撤销调用点各一处，失败路径绝不自动重签或轮换。"""
    source = _read(CARD_TSX)
    assert "webchatTokenErrorKey(error)" in source, "错误码只经 service 的纯映射函数"
    assert "props.onChanged()" in source, "失败/成功都以父级重读的元信息为准"
    assert source.count("issueWebchatToken(") == 1, "签发调用点只能有一处（失败路径不得自动重签）"
    assert source.count("revokeWebchatToken(") == 1
    for forbidden in ("error.response", "REVISION_CONFLICT", "setTimeout", "retry"):
        assert forbidden not in source, f"卡片不得自行重试/手搓错误码：{forbidden}"
    assert "Date.now" not in source, "到期时间只展示后端值，不在前端自行计算"


def test_card_uses_shared_components_and_localized_copy() -> None:
    """[RULE-ui-001]/[RULE-ui-detail-001] 共享组件、唯一状态映射、全词条化文案。"""
    source = _read(CARD_TSX)
    for component in (
        "components/common/ConfirmAction",
        "components/common/CopyButton",
        "components/common/DateTimeText",
        "components/common/DetailGrid",
        "components/common/StatusTag",
    ):
        assert component in source, f"缺少共享组件：{component}"
    assert "webchatTokenStatusOptions(t)" in source, "状态枚举必须走唯一一份 StatusTag 映射"
    assert 'data-testid="webchat-token-card"' in source
    assert "props.metadata.issued_at" in source and "props.metadata.expires_at" in source

    stripped = _strip_comments(source)
    hardcoded = [
        text
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        for text in group
        if text and re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]", text)
    ]
    assert not hardcoded, f"卡片出现硬编码中文文案：{hardcoded}"


def test_user_detail_mounts_the_card_in_the_admin_identity_area() -> None:
    """卡片挂在用户详情 IM 身份区域（ADMIN 门控）：元信息由父级读取并带竞态守卫。"""
    source = _read(TABS_TSX)
    assert "<UserChatTokenCard" in source
    assert "getWebchatToken(props.userId)" in source, "元信息由父级经模块 service 读取"
    assert "requestSeq" in source and "seq !== requestSeq.current" in source, (
        "元信息加载必须有请求竞态守卫（切换用户时迟到响应不写状态）"
    )
    credentials_tab = source[source.index("function CredentialsTab") : source.index("function IdentityTab")]
    assert "<WebchatTokenSection" not in credentials_tab, (
        "项目平台凭据区域不得再挂 Token 卡片"
    )
    identity_tab = source[
        source.index("function IdentityTab") : source.index("export function UserDetailTabs")
    ]
    assert "<WebchatTokenSection" in identity_tab, "Token 卡片必须挂在用户详情的 IM 身份区域"
    assert "props.canManageToken ?" in identity_tab, "Token 卡片必须受 ADMIN 门控"
    assert "canManageToken={isAdmin}" in source, "父级按 ADMIN 角色传入门控"
    section = source[
        source.index("function WebchatTokenSection") : source.index("function CredentialsTab")
    ]
    assert "user.webchatToken.title" in section
    assert "metadata={" in source and "onChanged={" in source


def test_card_compiles_to_a_real_typescript_artifact(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（含模块内 service 依赖）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "card.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(CARD_TSX),
            "--bundle",
            "--format=esm",
            "--loader:.tsx=tsx",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-i18next",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"卡片编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    assert "webchat-token-issued" in artifact, "编译产物必须包含卡片本身（不是空转）"
    assert CANARY not in artifact and RESET_CANARY not in artifact


def test_token_keys_are_localized() -> None:
    """Token 词条 zh-CN 与 en-US 齐平；状态词条不得是裸枚举码。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in (
        "user.webchatToken.status.NOT_CONFIGURED",
        "user.webchatToken.status.ACTIVE",
        "user.webchatToken.status.EXPIRED",
        "user.webchatToken.status.REVOKED",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
        assert zh[key] != key.rsplit(".", 1)[-1], f"{key} 是裸枚举码"
    for key in ("user.webchatToken.confirmReset", "user.webchatToken.confirmRevoke"):
        assert "会话" in zh[key], f"{key} 必须明示关联登录会话将失效"
        assert "session" in en[key].lower(), f"{key} 英文必须明示会话失效"
