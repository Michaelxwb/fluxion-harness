"""[B-292] 模型管理实际页面挂接 Key 池与额度操作（design frontend §3.2/§3.3/§3.7；backend API-01/02/13/16/17/18）。

真实边界：真实模块 service（`modules/model-management/services/models.ts`）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器）、真实页面/详情/表单 TSX 的 esbuild 编译产物与真实
node 执行；DOM 交互（打开资源池、点击新增/编辑/解绑）留真实浏览器 E2E（S-106，TASK-115）。

断言覆盖：

- **挂接（不遗留未挂载孤立组件）**：主列表 `ModelPage.tsx` 挂 TASK-086 凭据面板与 TASK-088 额度
  面板并传 `onChanged`；模型详情 `ModelDetailSideSheet.tsx` 挂 TASK-087 成员关系面板并把
  `onChanged` 透传回页面；**编译产物**里必须同时出现三个面板与入口 testid（证明它们在真实模块图
  里可达，不是只有文件存在）；
- **入口与权限**：资源池入口在 ADMIN 门控内（`account?.role === 'ADMIN'`），非 ADMIN 不渲染维护
  面；关闭资源池 SideSheet 只收起面板（不清空列表、不额外重载），沿用共享 `DetailSideSheet`；
- **字段面（最终新语义）**：逻辑模型读取只有 `provider_key`/`params`，写体是 `provider_key` +
  `params.max_tokens`（正整数）；`base_url`/`api_key`/`api_key_configured` 既不在读取也不在写体，
  表单里没有这两个输入框（连接与密钥的唯一权威是物理凭据，TASK-086）；
- **版本/测试保持新语义**：启用切换带 `expected_revision`（CAS）且只提交该字段；批量测试入口不变。
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
SERVICE_TS = MODULE / "services/models.ts"
PAGE_TSX = MODULE / "ModelPage.tsx"
SHEET_TSX = MODULE / "ModelDetailSideSheet.tsx"
FORM_TSX = MODULE / "ModelFormModal.tsx"
ICONS_TSX = MODULE / "ModelStatusIcons.tsx"
CLIENT_TS = SRC / "api/client.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CANARY = "sk-canary-292"
MODEL_ID = "33333333-3333-4333-8333-333333333333"

#: 新词条：资源池入口/挂接与最终字段面的全部文案（两侧必须齐平）。
REQUIRED_KEYS = (
    "model.columns.provider",
    "model.provider.declared",
    "model.provider.missing",
    "model.form.provider",
    "model.form.providerHint",
    "model.form.maxTokens",
    "model.form.maxTokensHint",
    "model.form.maxTokensInvalid",
    "model.resources.title",
    "model.resources.subtitle",
    "model.resources.manage",
    "model.resources.credentialsTab",
    "model.resources.quotasTab",
    "model.error.exists",
    "model.error.revisionConflict",
)

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

const mod = await import('./models.mjs');
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
      timestamp: '2026-10-19T00:00:00Z'
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

const model = {
  id: MODEL_ID,
  key: 'deepseek-chat',
  name: 'DeepSeek Chat',
  protocol: 'OPENAI',
  model_id: 'deepseek-chat',
  provider_key: 'deepseek',
  params: { max_tokens: 2048, temperature: 0.2 },
  revision: 4,
  enabled: true,
  last_test_status: 'UNTESTED',
  last_test_at: null,
  create_time: '2026-10-19T00:00:00Z',
  update_time: '2026-10-19T00:00:00Z'
};

const formValues = {
  key: 'deepseek-chat',
  name: 'DeepSeek Chat',
  protocol: 'OPENAI',
  model_id: 'deepseek-chat',
  provider_key: ' deepseek ',
  max_tokens: 4096,
  enabled: true
};

const observations = {};

// 1) 列表读取：分页/筛选参数 + 新投影（provider_key/params）
payload = { items: [model], page: 1, page_size: 15, total: 1 };
observations.listResult = await service.listModels({ page: 1, page_size: 15, enabled: 'true' });
observations.listRequest = observed();

// 2) 创建：provider_key + params.max_tokens
payload = model;
const createInput = service.buildModelCreateInput(formValues);
observations.createInput = createInput;
observations.createResult = await service.createModel(createInput);
observations.createRequest = observed();

// 3) 编辑：整份表单提交（params 合并保留其它键）+ CAS 版本
payload = { ...model, revision: 5, name: 'DeepSeek Chat Pro' };
const updateInput = service.buildModelUpdateInput(model, {
  ...formValues,
  name: 'DeepSeek Chat Pro',
  max_tokens: 8192
});
observations.updateInput = updateInput;
observations.updateResult = await service.updateModel(MODEL_ID, updateInput);
observations.updateRequest = observed();

// 4) 启用切换：单字段版本更新（不夹带任何连接/密钥字段）
payload = { ...model, revision: 5, enabled: false };
observations.toggleResult = await service.updateModel(MODEL_ID, {
  expected_revision: 4,
  enabled: false
});
observations.toggleRequest = observed();

// 5) 删除与批量测试（原入口保持）
payload = { id: MODEL_ID, deleted: true };
await service.deleteModel(MODEL_ID);
observations.deleteRequest = observed();

payload = {
  items: [
    {
      model_id: MODEL_ID,
      status: 'AVAILABLE',
      latency_ms: 12,
      error_code: null,
      tested_at: '2026-10-19T00:00:00Z'
    }
  ]
};
observations.testResults = await service.batchTestModels([MODEL_ID]);
observations.testRequest = observed();

// 6) 输入边界显式失败
observations.builders = {
  blankKey: captureError(() => service.buildModelCreateInput({ ...formValues, key: '   ' })),
  blankName: captureError(() => service.buildModelCreateInput({ ...formValues, name: '' })),
  blankModelId: captureError(() => service.buildModelCreateInput({ ...formValues, model_id: '' })),
  zeroBudget: captureError(() => service.buildModelCreateInput({ ...formValues, max_tokens: 0 })),
  negativeBudget: captureError(() => service.buildModelCreateInput({ ...formValues, max_tokens: -1 })),
  fractionalBudget: captureError(() => service.buildModelCreateInput({ ...formValues, max_tokens: 2.5 })),
  stringBudget: captureError(() => service.buildModelCreateInput({ ...formValues, max_tokens: '4096' })),
  booleanBudget: captureError(() => service.buildModelCreateInput({ ...formValues, max_tokens: true })),
  missingBudget: captureError(() =>
    service.buildModelCreateInput({ ...formValues, max_tokens: undefined })
  ),
  updateBlankName: captureError(() => service.buildModelUpdateInput(model, { ...formValues, name: ' ' })),
  updateZeroBudget: captureError(() =>
    service.buildModelUpdateInput(model, { ...formValues, max_tokens: 0 })
  )
};

// 7) 冲突码 → 本地化文案键
observations.errors = {
  exists: service.modelSaveErrorKey({ response: { data: { code: 'MODEL_KEY_EXISTS', msg: '已存在' } } }),
  revision: service.modelSaveErrorKey({ code: 'REVISION_CONFLICT' }),
  unknown: service.modelSaveErrorKey(new Error('boom'))
};

// 8) 逻辑模型面不可夹带连接/密钥：旧字段即使被塞进表单值也不得进请求体
const smuggled = { ...formValues, base_url: CANARY, api_key: CANARY, api_key_configured: true };
const smuggledInput = service.buildModelCreateInput(smuggled);
observations.smuggledKeys = Object.keys(smuggledInput).sort();
await service.createModel(smuggledInput);
observations.smuggleRequest = observed();
observations.smuggledParamKeys = Object.keys(smuggledInput.params).sort();

observations.canaryInRequests = requests
  .filter((entry) => JSON.stringify([entry.url, entry.params, entry.body]).includes(CANARY))
  .map((entry) => `${entry.method} ${entry.url}`);

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

    (tmp_path / "entry.ts").write_text(
        ENTRY.format(service=SERVICE_TS, client=CLIENT_TS), encoding="utf-8"
    )
    # 共享传输只从 semi 取 Toast：给它一个真实可解析的最小替身（别名目标必须先存在）
    (tmp_path / "semi-stub.mjs").write_text(
        "export const Toast = { error() {}, success() {}, info() {}, warning() {} };\n",
        encoding="utf-8",
    )
    harness = HARNESS_JS.replace("__CANARY__", CANARY).replace("__MODEL_ID__", MODEL_ID)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'models.mjs'}",
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


def _compile_tsx(source: Path, outfile: Path) -> str:
    """真实 TSX → ESM 编译产物（外部化 UI 依赖；模块图内的模块 service 仍被真实打包）。"""
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(source),
            "--bundle",
            "--format=esm",
            "--loader:.tsx=tsx",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-dom",
            "--external:react-i18next",
            "--external:react-router-dom",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-ui/lib/es/form",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"编译失败 {source.name}：{completed.stderr}"
    return outfile.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def model_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("model-admin-wiring"))


def test_real_service_keeps_the_final_model_field_surface(model_service: dict[str, Any]) -> None:
    """写体是 `provider_key` + `params.max_tokens`：旧连接/密钥字段既不在体里，也不被夹带。"""
    listing = model_service["listRequest"]
    assert listing["method"] == "GET"
    assert listing["url"] == "/models"
    assert listing["params"] == {"page": 1, "page_size": 15, "enabled": "true"}
    assert model_service["listResult"]["items"][0]["provider_key"] == "deepseek"
    assert model_service["listResult"]["items"][0]["params"]["max_tokens"] == 2048

    created = model_service["createRequest"]
    assert created["method"] == "POST"
    assert created["url"] == "/models"
    assert set(created["body"]) == {
        "key",
        "name",
        "protocol",
        "model_id",
        "provider_key",
        "params",
        "enabled",
    }, "创建体只能是契约字段：没有 base_url/api_key，也不带 revision"
    assert created["body"]["provider_key"] == "deepseek", "供方标识按契约原样保存（只去首尾空白）"
    assert created["body"]["params"] == {"max_tokens": 4096}
    assert created["requestId"], "写操作仍走共享传输的 X-Request-Id"

    updated = model_service["updateRequest"]
    assert updated["method"] == "PUT"
    assert updated["url"] == f"/models/{MODEL_ID}"
    assert set(updated["body"]) == {
        "expected_revision",
        "name",
        "model_id",
        "provider_key",
        "params",
        "enabled",
    }, "更新体不夹带旧字段；标识（key）不可变更"
    assert updated["body"]["expected_revision"] == 4, "整份编辑必须带 CAS 版本"
    assert updated["body"]["params"] == {
        "max_tokens": 8192,
        "temperature": 0.2,
    }, "表单只改 max_tokens，其余 params 原样保留（不静默丢参数）"

    deleted = model_service["deleteRequest"]
    assert deleted["method"] == "DELETE" and deleted["url"] == f"/models/{MODEL_ID}"

    tested = model_service["testRequest"]
    assert tested["method"] == "POST" and tested["url"] == "/models/batch-test"
    assert tested["body"] == {"model_ids": [MODEL_ID]}
    assert model_service["testResults"][0]["status"] == "AVAILABLE"


def test_real_service_validates_the_output_budget_and_identity(model_service: dict[str, Any]) -> None:
    """`params.max_tokens` 是真正发送的输出预算：缺失/布尔/浮点/字符串/非正数一律显式拒绝。"""
    builders = model_service["builders"]
    assert builders["blankKey"] == "KEY_REQUIRED"
    assert builders["blankName"] == "NAME_REQUIRED"
    assert builders["blankModelId"] == "MODEL_ID_REQUIRED"
    assert builders["zeroBudget"] == "MAX_TOKENS_INVALID"
    assert builders["negativeBudget"] == "MAX_TOKENS_INVALID"
    assert builders["fractionalBudget"] == "MAX_TOKENS_INVALID"
    assert builders["stringBudget"] == "MAX_TOKENS_INVALID"
    assert builders["booleanBudget"] == "MAX_TOKENS_INVALID"
    assert builders["missingBudget"] == "MAX_TOKENS_INVALID"
    assert builders["updateBlankName"] == "NAME_REQUIRED"
    assert builders["updateZeroBudget"] == "MAX_TOKENS_INVALID"
    assert model_service["createInput"]["params"] == {"max_tokens": 4096}
    assert model_service["updateInput"]["expected_revision"] == 4


def test_real_service_never_accepts_connection_or_key_material(model_service: dict[str, Any]) -> None:
    """逻辑模型面不接收连接/密钥：即使表单值里塞了旧字段，也不进请求体、不进 URL。"""
    assert model_service["smuggledKeys"] == [
        "enabled",
        "key",
        "model_id",
        "name",
        "params",
        "protocol",
        "provider_key",
    ]
    assert model_service["smuggledParamKeys"] == ["max_tokens"]
    assert model_service["canaryInRequests"] == [], "明文/端点不得出现在逻辑模型的任何请求里"
    assert "base_url" not in model_service["createRequest"]["body"]
    assert "api_key" not in model_service["createRequest"]["body"]


def test_real_service_toggles_enabled_as_a_single_field_version_update(
    model_service: dict[str, Any],
) -> None:
    """启用切换只提交 `expected_revision` + `enabled`：不带 name/params，也不带任何连接字段。"""
    toggle = model_service["toggleRequest"]
    assert toggle["method"] == "PUT"
    assert toggle["url"] == f"/models/{MODEL_ID}"
    assert toggle["body"] == {"expected_revision": 4, "enabled": False}
    assert model_service["toggleResult"]["enabled"] is False


def test_real_service_maps_conflicts_to_explicit_copy(model_service: dict[str, Any]) -> None:
    """标识重复与版本冲突各有明确文案键；未知失败回落通用保存失败。"""
    assert model_service["errors"] == {
        "exists": "model.error.exists",
        "revision": "model.error.revisionConflict",
        "unknown": "common.saveFailed",
    }


def test_main_list_mounts_the_admin_panels_and_notifies_once() -> None:
    """[B-292] 主列表可进入物理 Key/额度管理：两个面板真实挂载，变更只经一个刷新点回流。"""
    source = _read(PAGE_TSX)
    assert "from './ModelCredentialPanel'" in source, "凭据面板必须被主列表真实引用"
    assert "from './ModelQuotaPanel'" in source, "额度面板必须被主列表真实引用"
    assert "<ModelCredentialPanel" in source and "<ModelQuotaPanel" in source, (
        "两个面板必须在页面 JSX 里渲染（不能只 import 不挂载）"
    )
    assert source.count("onChanged={afterModelChanged}") == 3, (
        "凭据面板/额度面板/模型详情三处都必须接同一个变更回流点"
    )
    assert re.search(r"constafterModelChanged=useCallback\(\(\):void=>\{voidreload\(\);", _compact(source)), (
        "变更回流必须重读服务端列表事实，不就地猜状态"
    )
    assert "useAuth" in source and "account?.role === 'ADMIN'" in source, "入口按登录主体判定 ADMIN"
    assert 'data-testid="open-model-resources"' in source, "主列表必须有资源池入口"
    assert re.search(r"isAdmin\s*\?\s*\(", source), "资源池入口必须在 ADMIN 门控内渲染"
    assert "DetailSideSheet" in source, "资源池沿用共享详情壳"
    assert 'itemKey="credentials"' in source and 'itemKey="quotas"' in source
    assert "activeTab={resourcesTab}" in source, "共享 DetailSideSheet 调用点必须显式给 activeTab"
    assert re.search(r"onCancel=\{\(\)=>setResourcesVisible\(false\)\}", _compact(source)), (
        "关闭资源池只收起面板：不清列表、不额外重载"
    )


def test_main_list_reads_only_the_final_model_facts() -> None:
    """[B-292] 原模型编辑/版本/测试保持最终新语义：列表读 `provider_key`，切换走 CAS 单字段。"""
    source = _read(PAGE_TSX)
    assert "model.columns.provider" in source and "dataIndex: 'provider_key'" in source
    assert "base_url" not in source, "逻辑模型已无连接字段（执行入口在成员凭据）"
    assert "api_key_configured" not in source, "逻辑模型不再回显密钥配置态"
    compact = _compact(source)
    assert re.search(
        r"updateModel\(model\.id,\{expected_revision:model\.revision,enabled:!model\.enabled\}\)",
        compact,
    ), "启用切换是单字段版本更新（CAS），不重发连接/密钥字段"
    assert 'data-testid="batch-test"' in source and "batchTestModels" in source, "批量测试入口保持"
    assert "ConfirmAction" in source and "deleteModel" in source, "删除入口保持"


def test_model_detail_mounts_members_and_passes_changes_back() -> None:
    """[B-292] 模型详情挂接成员关系（TASK-087 面板），成员变更回流到页面。"""
    sheet = _read(SHEET_TSX)
    assert "ModelDeploymentPanel" in sheet
    assert "onChanged={props.onChanged}" in sheet, "成员变更必须透传页面的变更回流点"
    assert re.search(r"isAdmin\s*\?\s*\(\s*<Tabs\.TabPane\s+itemKey=\"members\"", sheet), (
        "成员 Tab 必须在 ADMIN 门控内渲染"
    )
    page = _read(PAGE_TSX)
    assert "<ModelDetailSideSheet" in page and "onChanged={afterModelChanged}" in page, (
        "页面必须把成员变更接回列表刷新"
    )


def test_model_form_modal_matches_the_final_field_surface() -> None:
    """[B-292] 表单只提交 `provider_key` + `params.max_tokens`：没有 Base URL/API Key 输入。"""
    form = _read(FORM_TSX)
    assert 'field="provider_key"' in form and "model.form.provider" in form
    assert "model.form.providerHint" in form
    assert 'field="max_tokens"' in form and "model.form.maxTokens" in form
    assert "model.form.maxTokensHint" in form and "model.form.maxTokensInvalid" in form
    assert 'field="base_url"' not in form and 'field="api_key"' not in form, (
        "连接与密钥不在逻辑模型表单（唯一权威是物理凭据）"
    )
    assert "api_key" not in form and "base_url" not in form, "表单源码不得出现连接/密钥字段"
    assert "buildModelCreateInput" in form and "buildModelUpdateInput" in form, (
        "表单必须经 service 的纯构造器提交（字段面唯一来源）"
    )
    assert "readOutputBudget" in form, "编辑回显只认真正发送的键（params.max_tokens）"
    assert "ModelInputError" in form, "输入边界必须显式失败并落到字段文案"
    assert "components/common/FormModal" in form and "width={720}" in form
    assert "disabled" in form and 'initValue="OPENAI"' in form, "协议仍只读回显 OpenAI"


def test_model_status_icons_read_the_provider_fact() -> None:
    """[B-292] 列表状态图标不再读已删除的 `api_key_configured`，改读真实供方事实。"""
    icons = _read(ICONS_TSX)
    assert "api_key_configured" not in icons
    assert "model.provider_key" in icons
    assert "model.columns.provider" in icons
    assert "model.provider.declared" in icons and "model.provider.missing" in icons


def test_page_and_form_compile_to_real_typescript_artifacts(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（含模块 service 依赖）经 esbuild 编译成 ESM。

    产物里必须同时出现三个面板与入口 testid —— 挂接是真实模块图里的引用，不是孤立文件；
    并且旧字段与明文密钥都不在产物里。
    """
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    page_artifact = _compile_tsx(PAGE_TSX, tmp_path / "page.mjs")
    # esbuild 产物把 JSX 属性编译成 `"data-testid": "<值>"` 键值对，断言 testid 值本身
    for testid in (
        "open-model-resources",
        "credential-panel",
        "quota-panel",
        "model-member-panel",
    ):
        assert f'"{testid}"' in page_artifact, f"页面编译产物缺少挂接标记：{testid}"
    assert "ModelCredentialPanel" in page_artifact and "ModelQuotaPanel" in page_artifact
    assert "api_key_configured" not in page_artifact, "旧字段必须彻底退出页面模块图"
    assert CANARY not in page_artifact

    form_artifact = _compile_tsx(FORM_TSX, tmp_path / "form.mjs")
    assert "max_tokens" in form_artifact and "provider_key" in form_artifact
    assert "base_url" not in form_artifact and "api_key" not in form_artifact

    sheet_artifact = _compile_tsx(SHEET_TSX, tmp_path / "sheet.mjs")
    assert '"model-member-panel"' in sheet_artifact
    assert "readModelProviderKey" in sheet_artifact and "readOutputBudget" in sheet_artifact


def test_wiring_keys_are_localized() -> None:
    """资源池入口/挂接与最终字段面的词条 zh-CN 与 en-US 齐平，状态词条不得是裸枚举码。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in ("model.provider.declared", "model.provider.missing", "model.resources.title"):
        assert zh[key] != en[key], f"{key} 未本地化"
    assert zh["model.columns.provider"] != "provider_key", "状态列名不得是裸字段名"
