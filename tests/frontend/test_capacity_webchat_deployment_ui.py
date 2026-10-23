"""[B-287] 模型成员绑定、权重与逻辑模型预算界面（design frontend §3.2/§3.4；backend API-01/TASK-019）。

真实边界：真实模块 service（`modules/model-management/services/modelDeployments.ts`）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器）、仓库内 esbuild 编译产物与真实 node 执行；纯解析/状态
（单关系输入构造、CAS 版本、权重边界、错误码映射、`params.max_tokens` 预算与 `provider_key` 读取）
跑真实代码，DOM 交互留真实浏览器 E2E（TASK-115）。断言覆盖：

- **单关系绑定/解绑/权重版本修改**：POST 绑定一条、PUT 改一条（`expected_revision` CAS）、
  DELETE 解绑一条；不存在覆盖整个成员集合的写入口（RULE-rel-001：集合整体 PUT 由后端 405 拒绝）；
- **不重复填写 Key**：成员体只含 `{key,credential_id,weight,enabled}`，本面不出现 `api_key`/`base_url`；
  更换凭据是 DELETE 旧关系 + POST 新关系，PUT 不携带 `credential_id`；
- **显示后续新 Run 生效与最大输出预算**：面板说明保存后影响后续新 Run；模型详情按 TASK-019 契约
  读取 `provider_key` 与 `params.max_tokens`（正整数；缺失/非法显式「未声明」，不伪造默认值）；
- **成员面板挂进共享详情**：`ModelDetailSideSheet` 的成员 Tab（仅 ADMIN）、共享组件与 i18n。
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
SERVICE_TS = MODULE / "services/modelDeployments.ts"
PANEL_TSX = MODULE / "ModelDeploymentPanel.tsx"
SHEET_TSX = MODULE / "ModelDetailSideSheet.tsx"
CLIENT_TS = SRC / "api/client.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CANARY = "sk-canary-287"
MODEL_ID = "33333333-3333-4333-8333-333333333333"
DEPLOYMENT_ID = "44444444-4444-4444-8444-444444444444"
CREDENTIAL_ID = "11111111-1111-4111-8111-111111111111"

#: 新词条：成员维护面与详情预算/供方的全部文案（两侧必须齐平，见 test_member_keys_are_localized）。
REQUIRED_KEYS = (
    "model.detail.provider",
    "model.detail.outputBudget",
    "model.detail.outputBudgetMissing",
    "model.detail.members",
    "model.member.title",
    "model.member.add",
    "model.member.empty",
    "model.member.truncated",
    "model.member.columns.key",
    "model.member.columns.credential",
    "model.member.columns.weight",
    "model.member.columns.enabled",
    "model.member.columns.revision",
    "model.member.columns.actions",
    "model.member.actions.edit",
    "model.member.actions.unbind",
    "model.member.confirmUnbind",
    "model.member.effectHint",
    "model.member.providerUndeclared",
    "model.member.noEligible",
    "model.member.credentialsUnavailable",
    "model.member.credentialsTruncated",
    "model.member.error.exists",
    "model.member.error.providerMismatch",
    "model.member.error.revisionConflict",
    "model.member.form.createTitle",
    "model.member.form.editTitle",
    "model.member.form.key",
    "model.member.form.credential",
    "model.member.form.credentialHint",
    "model.member.form.credentialRequired",
    "model.member.form.weight",
    "model.member.form.weightHint",
    "model.member.form.weightInvalid",
    "model.member.form.enabled",
    "model.member.form.capacityHint",
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

const MODEL_ID = '__MODEL_ID__';
const DEPLOYMENT_ID = '__DEPLOYMENT_ID__';
const CREDENTIAL_ID = '__CREDENTIAL_ID__';

const mod = await import('./deployments.mjs');
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

const member = {
  id: DEPLOYMENT_ID,
  key: 'deepseek-prod-a',
  model_id: MODEL_ID,
  credential_id: CREDENTIAL_ID,
  weight: 3,
  enabled: true,
  revision: 2
};

const formValues = {
  key: 'deepseek-prod-a',
  credential_id: CREDENTIAL_ID,
  weight: 5,
  enabled: true
};

const observations = {};

// 1) 列表读取：分页参数与关系投影
payload = { items: [member], page: 1, page_size: 100, total: 1 };
observations.listResult = await service.listDeployments(MODEL_ID, { page: 1, page_size: 100 });
observations.listRequest = observed();

// 2) 绑定：POST 单关系，只引用已配置凭据
payload = member;
const createInput = service.buildDeploymentCreateInput(formValues);
observations.createInput = createInput;
observations.createResult = await service.createDeployment(MODEL_ID, createInput);
observations.createRequest = observed();

// 3) 权重版本修改：单关系 PUT + CAS
payload = { ...member, weight: 5, revision: 3 };
const updateInput = service.buildDeploymentUpdateInput(member, formValues);
observations.updateInput = updateInput;
observations.updateResult = await service.updateDeployment(MODEL_ID, DEPLOYMENT_ID, updateInput);
observations.updateRequest = observed();

// 4) 解绑：DELETE 单关系
payload = { id: DEPLOYMENT_ID, deleted: true };
await service.deleteDeployment(MODEL_ID, DEPLOYMENT_ID);
observations.deleteRequest = observed();

// 5) 输入边界：权重是 1..100 正整数；绑定必须选一条已配置凭据
observations.builders = {
  weightZero: captureError(() => service.buildDeploymentCreateInput({ ...formValues, weight: 0 })),
  weightTooHigh: captureError(() => service.buildDeploymentCreateInput({ ...formValues, weight: 101 })),
  weightFractional: captureError(() => service.buildDeploymentCreateInput({ ...formValues, weight: 2.5 })),
  credentialBlank: captureError(() =>
    service.buildDeploymentCreateInput({ ...formValues, credential_id: '   ' })
  ),
  updateWeightInvalid: captureError(() =>
    service.buildDeploymentUpdateInput(member, { ...formValues, weight: 0 })
  )
};

// 6) 冲突码 → 本地化文案键
observations.errors = {
  exists: service.deploymentSaveErrorKey({
    response: { data: { code: 'MODEL_DEPLOYMENT_EXISTS', msg: '已存在' } }
  }),
  provider: service.deploymentSaveErrorKey({
    response: { data: { code: 'MODEL_PROVIDER_MISMATCH', msg: '供方不一致' } }
  }),
  revision: service.deploymentSaveErrorKey({ code: 'REVISION_CONFLICT' }),
  unknown: service.deploymentSaveErrorKey(new Error('boom'))
};

// 7) 逻辑模型事实：输出预算（params.max_tokens）与供方（provider_key）
observations.budget = {
  ok: service.readOutputBudget({ max_tokens: 2048, temperature: 0.2 }),
  missing: service.readOutputBudget({}),
  zero: service.readOutputBudget({ max_tokens: 0 }),
  negative: service.readOutputBudget({ max_tokens: -1 }),
  fractional: service.readOutputBudget({ max_tokens: 2.5 }),
  string: service.readOutputBudget({ max_tokens: '2048' }),
  boolean: service.readOutputBudget({ max_tokens: true }),
  synonymOnly: service.readOutputBudget({ max_output_tokens: 2048 })
};
observations.provider = {
  declared: service.readModelProviderKey({ provider_key: ' deepseek ' }),
  undeclared: service.readModelProviderKey({ provider_key: '' }),
  missing: service.readModelProviderKey({ key: 'model-1' }),
  wrongType: service.readModelProviderKey({ provider_key: 7 })
};
observations.memberKey = {
  direct: service.suggestMemberKey('deep', []),
  suffixed: service.suggestMemberKey('deep', ['deep']),
  nextSuffix: service.suggestMemberKey('deep', ['deep', 'deep-2']),
  longLength: service.suggestMemberKey('x'.repeat(140), ['x'.repeat(140)]).length
};

// 8) 单关系口径：任何时刻都没有集合级写请求，成员体也不夹带密钥/端点字段
observations.functions = Object.entries(service)
  .filter(([, value]) => typeof value === 'function')
  .map(([name]) => name)
  .sort();
observations.memberWrites = requests
  .filter((entry) => entry.method !== 'GET')
  .map((entry) => `${entry.method} ${entry.url}`);
observations.collectionWrites = requests
  .filter((entry) => entry.method === 'PUT' && entry.url === `/models/${MODEL_ID}/deployments`)
  .map((entry) => `${entry.method} ${entry.url}`);
observations.forbiddenFields = requests
  .filter((entry) => /api_key|base_url|apiKey/.test(JSON.stringify(entry.body ?? null)))
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

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(ENTRY.format(service=SERVICE_TS, client=CLIENT_TS), encoding="utf-8")
    harness = (
        HARNESS_JS.replace("__MODEL_ID__", MODEL_ID)
        .replace("__DEPLOYMENT_ID__", DEPLOYMENT_ID)
        .replace("__CREDENTIAL_ID__", CREDENTIAL_ID)
    )
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'deployments.mjs'}",
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
def deployment_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("deployment-ui"))


def test_real_service_maps_single_member_relations(deployment_service: dict[str, Any]) -> None:
    """API-01 单关系面：列表/绑定/权重版本修改/解绑，写操作带请求追踪 ID。"""
    listing = deployment_service["listRequest"]
    assert listing["method"] == "GET"
    assert listing["url"] == f"/models/{MODEL_ID}/deployments"
    assert listing["params"] == {"page": 1, "page_size": 100}
    assert listing["body"] is None
    assert deployment_service["listResult"]["total"] == 1
    projection = deployment_service["listResult"]["items"][0]
    assert set(projection) == {
        "id",
        "key",
        "model_id",
        "credential_id",
        "weight",
        "enabled",
        "revision",
    }, "成员投影恰为关系字段：不含密钥/端点"

    created = deployment_service["createRequest"]
    assert created["method"] == "POST"
    assert created["url"] == f"/models/{MODEL_ID}/deployments"
    assert created["requestId"], "写操作仍走共享传输的 X-Request-Id"
    assert set(created["body"]) == {"key", "credential_id", "weight", "enabled"}, (
        "绑定体只能是契约字段：不携带 revision，也不夹带密钥/端点"
    )
    assert created["body"]["credential_id"] == CREDENTIAL_ID, "绑定只引用已配置凭据"
    assert created["body"]["weight"] == 5

    updated = deployment_service["updateRequest"]
    assert updated["method"] == "PUT"
    assert updated["url"] == f"/models/{MODEL_ID}/deployments/{DEPLOYMENT_ID}"
    assert updated["body"]["expected_revision"] == 2, "权重版本修改必须带 CAS 版本"
    assert updated["body"]["weight"] == 5
    assert updated["body"]["key"] == "deepseek-prod-a"

    deleted = deployment_service["deleteRequest"]
    assert deleted["method"] == "DELETE"
    assert deleted["url"] == f"/models/{MODEL_ID}/deployments/{DEPLOYMENT_ID}"


def test_real_service_never_replaces_the_member_set_or_refills_keys(
    deployment_service: dict[str, Any],
) -> None:
    """单关系口径（RULE-rel-001）：无集合级写入口；成员体不出现密钥/端点字段。"""
    assert deployment_service["collectionWrites"] == [], (
        "成员集合整体 PUT 不是本面能力（后端 405）；重绑是 DELETE 旧关系 + POST 新关系"
    )
    assert deployment_service["memberWrites"] == [
        f"POST /models/{MODEL_ID}/deployments",
        f"PUT /models/{MODEL_ID}/deployments/{DEPLOYMENT_ID}",
        f"DELETE /models/{MODEL_ID}/deployments/{DEPLOYMENT_ID}",
    ]
    assert "credential_id" not in deployment_service["updateInput"], (
        "PUT 不携带 credential_id：更换凭据必须走 DELETE + POST 两条关系操作"
    )
    assert deployment_service["forbiddenFields"] == [], "成员面不得提交 api_key/base_url"
    assert deployment_service["functions"] == [
        "ModelDeploymentInputError",
        "buildDeploymentCreateInput",
        "buildDeploymentUpdateInput",
        "createDeployment",
        "deleteDeployment",
        "deploymentSaveErrorKey",
        "listDeployments",
        "readModelProviderKey",
        "readOutputBudget",
        "suggestMemberKey",
        "updateDeployment",
    ], "成员 service 只暴露单关系入口：不存在覆盖整个成员集合的写函数"


def test_real_service_validates_weight_and_credential_choice(deployment_service: dict[str, Any]) -> None:
    """输入边界显式失败：权重是 1..100 正整数；绑定必须选一条已配置凭据。"""
    builders = deployment_service["builders"]
    assert builders["weightZero"] == "WEIGHT_INVALID"
    assert builders["weightTooHigh"] == "WEIGHT_INVALID"
    assert builders["weightFractional"] == "WEIGHT_INVALID"
    assert builders["credentialBlank"] == "CREDENTIAL_REQUIRED"
    assert builders["updateWeightInvalid"] == "WEIGHT_INVALID"
    assert deployment_service["createInput"]["key"] == "deepseek-prod-a"
    assert deployment_service["updateInput"]["expected_revision"] == 2
    assert deployment_service["updateInput"]["enabled"] is True


def test_real_service_maps_conflicts_and_reads_the_output_budget(deployment_service: dict[str, Any]) -> None:
    """冲突码各有明确文案键；输出预算/供方按 TASK-019 契约读取，非法值显式「未声明」。"""
    assert deployment_service["errors"] == {
        "exists": "model.member.error.exists",
        "provider": "model.member.error.providerMismatch",
        "revision": "model.member.error.revisionConflict",
        "unknown": "common.saveFailed",
    }
    assert deployment_service["budget"] == {
        "ok": 2048,
        "missing": None,
        "zero": None,
        "negative": None,
        "fractional": None,
        "string": None,
        "boolean": None,
        "synonymOnly": None,
    }, "params.max_tokens 必须是正整数；缺失/非法一律显式未声明，不伪造默认值"
    assert deployment_service["provider"] == {
        "declared": "deepseek",
        "undeclared": "",
        "missing": "",
        "wrongType": "",
    }
    assert deployment_service["memberKey"] == {
        "direct": "deep",
        "suffixed": "deep-2",
        "nextSuffix": "deep-3",
        "longLength": 128,
    }, "成员标识默认取 Key 标识；重名追加后缀且不超过 ResourceKey 的 128 上限"


def test_member_panel_is_admin_gated_and_never_refills_keys() -> None:
    """[RULE-front-001] 成员维护入口仅 ADMIN；成员面只选已配置凭据，不重复填写密钥。"""
    source = _read(PANEL_TSX)
    assert "useAuth" in source
    assert "account?.role === 'ADMIN'" in source
    assert re.search(r"if\(!isAdmin\)\{returnnull;?\}", _compact(source)), (
        "非 ADMIN 必须直接不渲染维护面（不是只跳过请求）"
    )
    assert "ModelDeploymentForm" in source, "绑定/权重编辑必须以共享 FormModal 承载"
    assert "createDeployment(" in source and "updateDeployment(" in source and "deleteDeployment(" in source
    assert "listDeployments(" in source
    assert "deploymentSaveErrorKey" in source, "冲突码只经 service 的纯映射函数"
    assert "error.response" not in source, "组件不得手搓错误码提取（只走 apiErrorBody 的封装）"
    stripped = _strip_comments(source)
    for leaked in ("api_key", "base_url", "apiKey"):
        assert leaked not in stripped, f"成员面不得出现密钥/端点字段：{leaked}"
    assert 'field="api_key"' not in stripped, "成员表单不得出现密钥输入"
    assert 'data-testid="model-member-panel"' in source
    assert 'data-testid="bind-member"' in source


def test_member_panel_shows_effect_eligibility_and_binding_state() -> None:
    """展示后续新 Run 生效、可绑定集合（同供方/启用/未绑定）与未声明供方的显式降级。"""
    source = _read(PANEL_TSX)
    assert "model.member.effectHint" in source, "必须说明保存后影响后续新 Run"
    assert "model.member.providerUndeclared" in source, "未声明供方时必须显式说明无法绑定"
    assert "model.member.noEligible" in source, "没有可绑定凭据时弹窗必须说明并禁用确认"
    assert "credential.provider_key === props.providerKey" in source, (
        "可绑定集合只取同供方凭据（服务端同供方核对的前端一半）"
    )
    assert "boundIds.has(credential.id)" in source, "已绑定凭据不得重复出现在可绑定集合"
    assert "model.member.truncated" in source, "成员列表被截断时必须显式说明"
    assert "model.member.credentialsTruncated" in source, "凭据候选被截断时必须显式说明"
    assert "model.member.credentialsUnavailable" in source and "credentials.failed" in source, (
        "凭据索引加载失败必须显式降级（标签不可用 + 绑定入口禁用）"
    )
    assert "model.member.columns.weight" in source and "MEMBER_WEIGHT_MAX" in source
    assert "ConfirmAction" in source and "model.member.confirmUnbind" in source, "解绑必须走确认"
    assert "suggestMemberKey" in source and "existingMemberKeys" in source, (
        "选中物理 Key 后必须连带成员标识，不再要求手填第二遍"
    )
    assert "suggestMemberKey(selected.key" in source, "自动填充必须落到成员标识字段"
    assert "model.member.form.keyHint" in source, "必须说明成员标识由物理 Key 自动生成"
    key_field = source.index('field="key"')
    assert "disabled" in source[key_field : key_field + 220], (
        "成员标识是派生展示值，不允许再手填/改名"
    )


def test_member_panel_uses_shared_components_guards_and_i18n() -> None:
    """[RULE-ui-001]/[RULE-ui-detail-001] 共享组件、请求竞态守卫、词条化文案。"""
    source = _read(PANEL_TSX)
    for component in (
        "components/common/DataTable",
        "components/common/FormModal",
        "components/common/ConfirmAction",
        "components/common/ErrorState",
        "components/common/EmptyState",
        "components/common/StatusTag",
        "components/common/TableText",
        "components/common/tableLayout",
    ):
        assert component in source, f"缺少共享组件：{component}"
    assert "requestSeq" in source and "seq !== requestSeq.current" in source, "成员列表加载必须有请求竞态守卫"
    assert "detail-section-title" in source and "detail-hint" in source, (
        "关系 Tab 统一「工具行 → 分区标题 → 表格 → 底部说明文案」"
    )

    stripped = _strip_comments(source)
    hardcoded = [
        text
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        for text in group
        if text and re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]", text)
    ]
    assert not hardcoded, f"面板出现硬编码中文文案：{hardcoded}"


def test_detail_sheet_mounts_member_tab_with_budget_facts() -> None:
    """成员面板挂进共享详情（仅 ADMIN）；详情按新事实读 `provider_key`/`params.max_tokens`。"""
    source = _read(SHEET_TSX)
    assert "ModelDeploymentPanel" in source
    assert re.search(r"isAdmin\s*\?\s*\(\s*<Tabs\.TabPane\s+itemKey=\"members\"", source), (
        "成员 Tab 必须在 ADMIN 门控内渲染"
    )
    assert "modelId={model.id}" in source
    assert "providerKey={providerKey}" in source
    assert "readModelProviderKey" in source and "readOutputBudget" in source
    assert "model.detail.outputBudget" in source and "model.detail.outputBudgetMissing" in source
    assert "model.detail.provider" in source
    assert "DetailGrid" in source and "StatusTag" in source and "model.detail.hint" in source
    assert "api_key_configured" not in source, "模型详情不得再读已删除的旧字段（DTO 同步属 TASK-092）"
    assert "model.form.baseUrl" not in source, "Base URL 已不在逻辑模型上（执行入口在成员凭据）"


def test_member_panel_compiles_to_real_typescript_artifacts(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（面板 + 详情）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    for name, source in (("panel", PANEL_TSX), ("sheet", SHEET_TSX)):
        outfile = tmp_path / f"{name}.mjs"
        completed = subprocess.run(
            [
                str(ESBUILD),
                str(source),
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
        assert completed.returncode == 0, f"{name} 编译失败：{completed.stderr}"
        artifact = outfile.read_text(encoding="utf-8")
        assert CANARY not in artifact
    assert "model-member-panel" in (tmp_path / "panel.mjs").read_text(encoding="utf-8"), (
        "编译产物必须包含面板本身（不是空转）"
    )
    assert "model.detail.members" in (tmp_path / "sheet.mjs").read_text(encoding="utf-8")


def test_member_keys_are_localized() -> None:
    """成员/预算词条 zh-CN 与 en-US 齐平，且不是照抄的中文原文。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in (
        "model.detail.provider",
        "model.detail.outputBudget",
        "model.detail.members",
        "model.member.add",
        "model.member.effectHint",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
