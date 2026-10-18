"""[B-286] 模型物理 Key 管理与检测界面（design frontend §3.2/§3.4/§3.7；backend §3.4 API-16/API-17）。

真实边界：真实模块 service（`modules/model-management/services/modelCredentials.ts`）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器）、仓库内 esbuild 编译产物与真实 node 执行；纯解析/状态
（DTO 映射、留空保留/显式替换、错误码映射）跑真实代码，DOM 交互留真实浏览器 E2E（S-106，TASK-115）。
断言覆盖：

- **ADMIN 凭据维护面**：列表 / 创建（`Idempotency-Key`）/ 整份替换（CAS `expected_revision`）/ 删除，
  以及凭据表单归属选择需要的额度组选项读取；
- **读取只 `configured`**：明文密钥只出现在写入体，绝不出现在列表/详情/检测的 URL、参数或响应渲染里；
- **空白 Key 保留、替换显式提交**：留空时更新体**不含** `api_key` 键，非空才显式带上；
- **检测只点名单个成员**：`(model_id, credential_id)` 一组一探，逐项结果原样保留（含 `NOT_EXECUTED`），
  不遍历模型×Key、不伪称整池健康、不改逻辑模型 `last_test_status`；
- **冲突显式**：同物理 Key 去重、降容/换归属需排空、版本冲突各有本地化文案；
- **面板源码契约**：仅 ADMIN 渲染、共享组件与 i18n、请求竞态守卫、真实 TSX 编译产物。
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
MODULE = SRC / "modules/model-management"
SERVICE_TS = MODULE / "services/modelCredentials.ts"
PANEL_TSX = MODULE / "ModelCredentialPanel.tsx"
CLIENT_TS = SRC / "api/client.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CANARY = "sk-canary-286"
MODEL_ID = "33333333-3333-4333-8333-333333333333"
CREDENTIAL_ID = "11111111-1111-4111-8111-111111111111"
QUOTA_GROUP_ID = "22222222-2222-4222-8222-222222222222"

#: 新词条：凭据维护/检测面的全部文案（两侧必须齐平，见 test_credential_keys_are_localized）。
REQUIRED_KEYS = (
    "model.credential.title",
    "model.credential.subtitle",
    "model.credential.add",
    "model.credential.empty",
    "model.credential.columns.key",
    "model.credential.columns.provider",
    "model.credential.columns.baseUrl",
    "model.credential.columns.quotaGroup",
    "model.credential.columns.maxInflight",
    "model.credential.columns.apiKey",
    "model.credential.columns.enabled",
    "model.credential.columns.revision",
    "model.credential.columns.actions",
    "model.credential.actions.edit",
    "model.credential.actions.delete",
    "model.credential.actions.test",
    "model.credential.quota.exclusive",
    "model.credential.confirmDelete",
    "model.credential.form.createTitle",
    "model.credential.form.editTitle",
    "model.credential.form.key",
    "model.credential.form.provider",
    "model.credential.form.providerHint",
    "model.credential.form.baseUrl",
    "model.credential.form.baseUrlHint",
    "model.credential.form.baseUrlInvalid",
    "model.credential.form.apiKey",
    "model.credential.form.apiKeyHint",
    "model.credential.form.apiKeyKeep",
    "model.credential.form.apiKeyNotice",
    "model.credential.form.apiKeyRequired",
    "model.credential.form.quotaGroup",
    "model.credential.form.quotaGroupHint",
    "model.credential.form.maxInflight",
    "model.credential.form.maxInflightHint",
    "model.credential.form.maxInflightInvalid",
    "model.credential.form.enabled",
    "model.credential.error.exists",
    "model.credential.error.drainRequired",
    "model.credential.error.inUse",
    "model.credential.error.revisionConflict",
    "model.credential.probe.title",
    "model.credential.probe.model",
    "model.credential.probe.modelHint",
    "model.credential.probe.run",
    "model.credential.probe.memberOnly",
    "model.credential.probe.pickerTruncated",
    "model.credential.probe.failed",
    "model.credential.probe.empty",
    "model.credential.probe.columns.model",
    "model.credential.probe.columns.status",
    "model.credential.probe.columns.latency",
    "model.credential.probe.columns.errorCode",
    "model.credential.probe.columns.testedAt",
    "model.credential.probe.status.AVAILABLE",
    "model.credential.probe.status.FAILED",
    "model.credential.probe.status.NOT_EXECUTED",
)

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Button = () => null;
export const Modal = () => null;
export const Select = () => null;
export const Table = () => null;
export const Banner = () => null;
export const Typography = { Paragraph: () => null, Text: () => null };
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
globalThis.window = { location: { pathname: '/models', search: '' } };

const CANARY = '__CANARY__';
const MODEL_ID = '__MODEL_ID__';
const CREDENTIAL_ID = '__CREDENTIAL_ID__';
const QUOTA_GROUP_ID = '__QUOTA_GROUP_ID__';

const mod = await import('./credentials.mjs');
const { api, ...service } = mod;

const requests = [];
let payload = null;

const headerMap = (config) => {
  const headers = config.headers;
  if (headers && typeof headers.toJSON === 'function') return headers.toJSON();
  return { ...headers };
};

api.defaults.adapter = async (config) => {
  requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    headers: headerMap(config),
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : null
  });
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
    idempotencyKey: entry.headers['Idempotency-Key'] ?? null,
    requestId: entry.headers['X-Request-Id'] ?? null,
    csrf: entry.headers['X-CSRF-Token'] ?? null
  };
};

const captureError = (run) => {
  try {
    run();
    return null;
  } catch (error) {
    return error?.reason ?? error?.name ?? 'error';
  }
};

const credential = {
  id: CREDENTIAL_ID,
  key: 'deepseek-prod',
  provider_key: 'deepseek',
  base_url: 'https://api.deepseek.com',
  quota_group_id: null,
  max_inflight: 8,
  enabled: true,
  revision: 3,
  configured: true
};

const formValues = {
  key: 'deepseek-prod',
  provider_key: 'deepseek',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  quota_group_id: QUOTA_GROUP_ID,
  max_inflight: 12,
  enabled: true
};

const observations = {};

// 1) 列表读取：分页参数与读取投影
payload = { items: [credential], page: 1, page_size: 20, total: 1 };
observations.listResult = await service.listCredentials({ page: 1, page_size: 20 });
observations.listRequest = observed();

// 2) 创建：幂等键由调用方持有并透传
payload = credential;
const createInput = service.buildCredentialCreateInput({ ...formValues, api_key: CANARY });
observations.createInput = createInput;
observations.createResult = await service.createCredential(createInput, 'idem-286');
observations.createRequest = observed();

// 3) 更新（留空 Key）：整份替换 + CAS 版本
payload = { ...credential, revision: 4, max_inflight: 12 };
const keepInput = service.buildCredentialUpdateInput(credential, { ...formValues, api_key: '   ' });
observations.updateKeepInput = keepInput;
observations.updateKeepResult = await service.updateCredential(CREDENTIAL_ID, keepInput);
observations.updateKeepRequest = observed();

// 4) 更新（显式替换 Key）
payload = { ...credential, revision: 5 };
const replaceInput = service.buildCredentialUpdateInput(credential, { ...formValues, api_key: CANARY });
observations.updateReplaceInput = replaceInput;
observations.updateReplaceResult = await service.updateCredential(CREDENTIAL_ID, replaceInput);
observations.updateReplaceRequest = observed();

// 5) 删除
payload = { id: CREDENTIAL_ID, deleted: true };
await service.deleteCredential(CREDENTIAL_ID);
observations.deleteRequest = observed();

// 6) 凭据表单的额度组归属选项
payload = {
  items: [
    { id: QUOTA_GROUP_ID, key: 'deepseek-shared', max_inflight: 16, rpm: 600, tpm: null, revision: 2 }
  ],
  page: 1,
  page_size: 100,
  total: 1
};
observations.quotaResult = await service.listQuotaGroups();
observations.quotaRequest = observed();

// 7) 检测：只点名一组 (model_id, credential_id)
payload = {
  items: [
    {
      model_id: MODEL_ID,
      credential_id: CREDENTIAL_ID,
      status: 'NOT_EXECUTED',
      error_code: 'MODEL_UNAVAILABLE',
      latency_ms: 3,
      tested_at: '2026-10-18T00:00:00Z'
    }
  ]
};
observations.probeResults = await service.probeCredentials([
  { model_id: MODEL_ID, credential_id: CREDENTIAL_ID }
]);
observations.probeRequest = observed();

// 明文只允许出现在写入体：URL/参数/其它请求体一律不得出现
observations.canaryInUrl = requests
  .filter((entry) => `${entry.url}${JSON.stringify(entry.params ?? null)}`.includes(CANARY))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.canaryInBody = requests
  .filter((entry) => JSON.stringify(entry.body ?? null).includes(CANARY))
  .map((entry) => `${entry.method} ${entry.url}`);

// 8) 纯映射：留空保留 / 显式替换 / 输入边界
observations.builders = {
  createBlankKey: captureError(() =>
    service.buildCredentialCreateInput({ ...formValues, api_key: '   ' })
  ),
  updateZeroInflight: captureError(() =>
    service.buildCredentialUpdateInput(credential, { ...formValues, max_inflight: 0 })
  ),
  updateFractionalInflight: captureError(() =>
    service.buildCredentialUpdateInput(credential, { ...formValues, max_inflight: 2.5 })
  )
};

// 9) 冲突码 → 本地化文案键
observations.errors = {
  exists: service.credentialSaveErrorKey({
    response: { data: { code: 'MODEL_CREDENTIAL_EXISTS', msg: '已存在' } }
  }),
  drain: service.credentialSaveErrorKey({
    response: { data: { code: 'MODEL_CAPACITY_DRAIN_REQUIRED', msg: '需排空' } }
  }),
  inUse: service.credentialSaveErrorKey({ code: 'MODEL_CREDENTIAL_IN_USE' }),
  revision: service.credentialSaveErrorKey({
    response: { data: { code: 'REVISION_CONFLICT', msg: '版本冲突' } }
  }),
  unknown: service.credentialSaveErrorKey(new Error('boom'))
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
        .replace("__MODEL_ID__", MODEL_ID)
        .replace("__CREDENTIAL_ID__", CREDENTIAL_ID)
        .replace("__QUOTA_GROUP_ID__", QUOTA_GROUP_ID)
    )
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'credentials.mjs'}",
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
def credential_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("credential-ui"))


def test_real_service_maps_the_admin_credential_routes(credential_service: dict[str, Any]) -> None:
    """API-16 维护面：列表/创建/整份替换/删除，写操作带幂等键与请求追踪 ID。"""
    listing = credential_service["listRequest"]
    assert listing["method"] == "GET"
    assert listing["url"] == "/model-credentials"
    assert listing["params"] == {"page": 1, "page_size": 20}
    assert listing["body"] is None
    assert credential_service["listResult"]["total"] == 1
    assert credential_service["listResult"]["items"][0]["configured"] is True

    created = credential_service["createRequest"]
    assert created["method"] == "POST"
    assert created["url"] == "/model-credentials"
    assert created["idempotencyKey"] == "idem-286", "创建必须透传调用方持有的幂等键"
    assert created["requestId"], "写操作仍走共享传输的 X-Request-Id"
    assert set(created["body"]) == {
        "key",
        "provider_key",
        "base_url",
        "api_key",
        "quota_group_id",
        "max_inflight",
        "enabled",
    }, "创建体只能是契约字段：不携带 revision，也不夹带旧逻辑模型的连接/密钥字段"
    assert created["body"]["quota_group_id"] == QUOTA_GROUP_ID

    keep = credential_service["updateKeepRequest"]
    assert keep["method"] == "PUT"
    assert keep["url"] == f"/model-credentials/{CREDENTIAL_ID}"
    assert keep["body"]["expected_revision"] == 3, "整份替换必须带 CAS 版本"
    assert keep["body"]["key"] == "deepseek-prod", "标识在编辑时不可变更"

    deleted = credential_service["deleteRequest"]
    assert deleted["method"] == "DELETE"
    assert deleted["url"] == f"/model-credentials/{CREDENTIAL_ID}"

    quota = credential_service["quotaRequest"]
    assert quota["method"] == "GET"
    assert quota["url"] == "/model-quota-groups"
    assert credential_service["quotaResult"]["items"][0]["key"] == "deepseek-shared"


def test_real_service_keeps_the_key_write_only(credential_service: dict[str, Any]) -> None:
    """空白 Key 保留、替换显式提交；明文只在写入体出现，读取面不回显。"""
    assert credential_service["createRequest"]["body"]["api_key"] == CANARY
    assert credential_service["updateReplaceRequest"]["body"]["api_key"] == CANARY

    keep_body = credential_service["updateKeepRequest"]["body"]
    assert "api_key" not in keep_body, "留空表示保留原值：更新体连 api_key 键都不发"
    assert "api_key" not in credential_service["updateKeepInput"]

    assert credential_service["canaryInUrl"] == [], "明文不得进入 URL 或查询参数"
    assert credential_service["canaryInBody"] == [
        "POST /model-credentials",
        f"PUT /model-credentials/{CREDENTIAL_ID}",
    ], "明文只允许出现在写入体"
    assert credential_service["listResult"]["items"][0].get("api_key") is None
    assert credential_service["createResult"]["configured"] is True
    assert credential_service["updateKeepResult"]["configured"] is True


def test_real_service_probes_only_the_named_member(credential_service: dict[str, Any]) -> None:
    """检测面：请求只含被点名的 (model_id, credential_id)，逐项结果原样保留。"""
    probe = credential_service["probeRequest"]
    assert probe["method"] == "POST"
    assert probe["url"] == "/model-credentials/test"
    assert probe["body"] == {
        "targets": [{"model_id": MODEL_ID, "credential_id": CREDENTIAL_ID}]
    }, "检测只点名显式成员：不自动遍历模型×Key，也不自报计量身份"

    results = credential_service["probeResults"]
    assert [item["status"] for item in results] == ["NOT_EXECUTED"], (
        "未执行（未申请到容量）必须原样保留，不能折叠成失败/可用"
    )
    assert results[0]["error_code"] == "MODEL_UNAVAILABLE"
    assert results[0]["credential_id"] == CREDENTIAL_ID
    assert results[0]["latency_ms"] == 3


def test_real_service_validates_the_credential_input(credential_service: dict[str, Any]) -> None:
    """输入边界显式失败：新增必须给密钥；在飞上限必须是正整数。"""
    builders = credential_service["builders"]
    assert builders["createBlankKey"] == "API_KEY_REQUIRED"
    assert builders["updateZeroInflight"] == "MAX_INFLIGHT_INVALID"
    assert builders["updateFractionalInflight"] == "MAX_INFLIGHT_INVALID"
    assert credential_service["createInput"]["max_inflight"] == 12
    assert credential_service["createInput"]["base_url"] == "https://api.deepseek.com/v1"
    assert credential_service["updateKeepInput"]["quota_group_id"] == QUOTA_GROUP_ID


def test_real_service_maps_conflicts_to_explicit_copy(credential_service: dict[str, Any]) -> None:
    """同物理 Key 去重、降容/换归属排空、版本冲突与未知失败各有明确文案键。"""
    errors = credential_service["errors"]
    assert errors == {
        "exists": "model.credential.error.exists",
        "drain": "model.credential.error.drainRequired",
        "inUse": "model.credential.error.inUse",
        "revision": "model.credential.error.revisionConflict",
        "unknown": "common.saveFailed",
    }


def test_panel_is_admin_gated_and_never_renders_key_material() -> None:
    """[RULE-front-001] 凭据维护入口仅 ADMIN；读取只表达 `configured`，不渲染密钥字段。"""
    source = _read(PANEL_TSX)
    assert "useAuth" in source
    assert "account?.role === 'ADMIN'" in source
    assert re.search(r"if\(!isAdmin\)\{returnnull;?\}", _compact(source)), (
        "非 ADMIN 必须直接不渲染维护面（不是只跳过请求）"
    )
    assert "apiKeyOptions(t)" in source and "credential.configured" in source, "读取只表达 configured"
    for leaked in ("credential.api_key", "record.api_key", "item.api_key", "api_key_configured"):
        assert leaked not in source, f"面板不得读取密钥字段：{leaked}"
    assert "model.credential.form.apiKeyKeep" in source, "编辑时留空保留原值必须有说明"
    assert "model.credential.form.apiKeyNotice" in source, "必须说明密钥不回显"
    assert "api_key: ''" in source, "编辑表单的密钥输入必须从空值开始"


def test_panel_probe_shows_single_member_results_only() -> None:
    """单成员检测：只发一组目标、结果逐项展示，不显示整池健康、不改逻辑模型测试状态。"""
    source = _read(PANEL_TSX)
    assert "probeCredentials([{ model_id: modelId, credential_id: credential.id }])" in source, (
        "检测必须只点名当前凭据 + 所选模型这一组成员"
    )
    assert "model.credential.probe.memberOnly" in source, "必须明示结果只覆盖被点名的成员"
    assert "model.credential.probe.pickerTruncated" in source, "模型候选被截断时必须显式说明"
    for forbidden in ("batchTestModels", "last_test_status", "api_key_configured"):
        assert forbidden not in source, f"检测面不得出现整池/逻辑模型语义：{forbidden}"
    assert "probeStatusOptions(t)" in source, "检测状态必须走统一枚举映射"


def test_panel_uses_shared_components_guards_and_i18n() -> None:
    """[RULE-ui-001]/[RULE-ui-detail-001] 共享组件、请求竞态守卫、词条化文案。"""
    source = _read(PANEL_TSX)
    for component in (
        "components/common/DataTable",
        "components/common/FormModal",
        "components/common/ModuleToolbar",
        "components/common/ConfirmAction",
        "components/common/ErrorState",
        "components/common/PaginationFooter",
        "components/common/StatusTag",
        "components/common/DateTimeText",
    ):
        assert component in source, f"缺少共享组件：{component}"
    assert "requestSeq" in source and "seq !== requestSeq.current" in source, (
        "列表加载必须有请求竞态守卫"
    )
    assert "credentialSaveErrorKey" in source, "冲突码只经 service 的纯映射函数"
    assert "error.response" not in source, "组件不得手搓错误码提取（只走 apiErrorBody 的封装）"
    assert "data-testid=\"credential-panel\"" in source

    stripped = _strip_comments(source)
    hardcoded = [
        text
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        for text in group
        if text and re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]", text)
    ]
    assert not hardcoded, f"面板出现硬编码中文文案：{hardcoded}"


def test_panel_compiles_to_a_real_typescript_artifact(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（含模块内 service 依赖）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "panel.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(PANEL_TSX),
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
    assert completed.returncode == 0, f"面板编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    assert "credential-panel" in artifact, "编译产物必须包含面板本身（不是空转）"
    assert CANARY not in artifact


def test_credential_keys_are_localized() -> None:
    """凭据/检测词条 zh-CN 与 en-US 齐平，状态词条不得是裸枚举码。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in (
        "model.credential.probe.status.AVAILABLE",
        "model.credential.probe.status.FAILED",
        "model.credential.probe.status.NOT_EXECUTED",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
        assert zh[key] != key.rsplit(".", 1)[-1], f"{key} 是裸枚举码"
    assert "configured" in zh["model.credential.subtitle"] or "已配置" in zh["model.credential.subtitle"]
