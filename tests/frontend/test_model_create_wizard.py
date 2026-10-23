"""新增模型两步向导：先解决物理 Key，再创建模型并自动绑定第一条成员。

真实边界：真实 service 模块（`services/modelOnboarding.ts` 及其依赖的 models/deployments/
credentials）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器）、真实 TSX 的 esbuild
编译产物。DOM 点击路径留给真实浏览器 E2E；本用例钉住写入编排与字段面契约。

关键断言：

- **顺序固定**：`POST /models` 之后才 `POST /models/{id}/deployments`，成员体只含
  `{key:<物理 Key 标识>, credential_id, weight:1, enabled:true}`，不复制密钥/端点；
- **供方由物理 Key 决定**：模型体里的 `provider_key` 取所选 Key，不信任表单里的同名字段；
- **绑定失败保留模型**：`MemberBindingFailedError` 携带已创建的模型与原错误，调用方可以
  重试绑定或去成员页；
- **响应丢失重试**：`MODEL_DEPLOYMENT_EXISTS` 按“已绑定”处理；
- **入口与文案**：ADMIN 的“新增模型”打开向导，资源准备按钮在新增按钮之前；向导两步、
  锁定供方、自动绑定、绑定失败重试；zh/en 词条齐平。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/model-management"
SERVICE_TS = MODULE / "services/modelOnboarding.ts"
CLIENT_TS = SRC / "api/client.ts"
WIZARD_TSX = MODULE / "ModelCreateWizard.tsx"
MODEL_FIELDS_TSX = MODULE / "ModelFields.tsx"
CREDENTIAL_FIELDS_TSX = MODULE / "CredentialFields.tsx"
PAGE_TSX = MODULE / "ModelPage.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

MODEL_ID = "44444444-4444-4444-8444-444444444444"
CREDENTIAL_ID = "55555555-5555-4555-8555-555555555555"

ENTRY = """export * from '{service}';
export {{ api }} from '{client}';
"""

HARNESS_JS = """
const toasts = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_csrf=console-csrf-value' };
globalThis.window = { location: { pathname: '/models', search: '', assign() {} } };

const MODEL_ID = '__MODEL_ID__';
const CREDENTIAL_ID = '__CREDENTIAL_ID__';

const mod = await import('./onboarding.mjs');
const { api, ...service } = mod;

const requests = [];
let queue = [];

const headerMap = (config) => {
  const headers = config.headers;
  if (headers && typeof headers.toJSON === 'function') return headers.toJSON();
  return { ...headers };
};

api.defaults.adapter = async (config) => {
  requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    headers: headerMap(config),
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : null
  });
  const next = queue.shift();
  if (!next) {
    throw new Error(`unexpected request: ${config.method} ${config.url}`);
  }
  return {
    data: {
      code: next.code ?? '0',
      msg: next.msg ?? 'ok',
      data: next.data ?? null,
      trace_id: 'trace',
      request_id: 'req',
      timestamp: '2026-10-23T00:00:00Z'
    },
    status: next.status ?? 200,
    statusText: next.status && next.status >= 400 ? 'Error' : 'OK',
    headers: {},
    config
  };
};

const credential = {
  id: CREDENTIAL_ID,
  key: 'deepseek-main',
  provider_key: 'deepseek',
  base_url: 'https://api.deepseek.com',
  quota_group_id: null,
  max_inflight: 8,
  enabled: true,
  revision: 1,
  configured: true
};

const model = {
  id: MODEL_ID,
  key: 'deepseek-chat',
  name: 'DeepSeek Chat',
  protocol: 'OPENAI',
  model_id: 'deepseek-chat',
  provider_key: 'deepseek',
  params: { max_tokens: 4096 },
  revision: 1,
  enabled: true,
  last_test_status: 'UNTESTED',
  last_test_at: null,
  create_time: '2026-10-23T00:00:00Z',
  update_time: '2026-10-23T00:00:00Z'
};

const member = {
  id: '66666666-6666-4666-8666-666666666666',
  key: 'deepseek-main',
  model_id: MODEL_ID,
  credential_id: CREDENTIAL_ID,
  weight: 1,
  enabled: true,
  revision: 1
};

const formValues = {
  key: 'deepseek-chat',
  name: 'DeepSeek Chat',
  protocol: 'OPENAI',
  model_id: 'deepseek-chat',
  provider_key: 'form-value-must-not-win',
  max_tokens: 4096,
  enabled: true
};

const observations = {};
const capture = (run) => {
  try {
    return { ok: true, value: run() };
  } catch (error) {
    return { ok: false, name: error?.name ?? 'error', reason: error?.reason ?? null };
  }
};

// 1) 成功路径：先建模型，再绑定以物理 Key 标识命名的成员
queue = [{ data: model }, { data: member }];
observations.success = await service.createModelWithMember(formValues, credential);
observations.successRequests = requests.splice(0, requests.length);

// 2) 绑定失败：模型已创建，错误里必须带回模型与原错误码
queue = [
  { data: { ...model, id: MODEL_ID } },
  { status: 409, code: 'MODEL_PROVIDER_MISMATCH', msg: '供方不一致' }
];
let bindingError = null;
try {
  await service.createModelWithMember(formValues, credential);
} catch (error) {
  bindingError = error;
}
observations.bindingFailure = {
  name: bindingError?.name ?? null,
  modelId: bindingError?.model?.id ?? null,
  reasonCode:
    bindingError?.reason?.response?.data?.code ?? bindingError?.reason?.code ?? null
};
observations.bindingFailureRequests = requests.splice(0, requests.length);

// 3) 响应丢失后的重试：MODEL_DEPLOYMENT_EXISTS 视为已绑定
queue = [{ status: 409, code: 'MODEL_DEPLOYMENT_EXISTS', msg: '已存在' }];
let alreadyBound = false;
try {
  await service.bindFirstMember(model, credential);
} catch (error) {
  alreadyBound = service.isMemberAlreadyBound(error);
}
observations.alreadyBound = alreadyBound;
observations.retryRequests = requests.splice(0, requests.length);

// 4) 纯构造器：供方取物理 Key，成员体不夹带密钥/端点
observations.modelInput = service.buildOnboardingModelInput(formValues, credential);
observations.memberInput = service.buildFirstMemberInput(credential);
observations.constructorErrors = {
  blankKey: capture(() =>
    service.buildOnboardingModelInput({ ...formValues, key: ' ' }, credential)
  ),
  zeroBudget: capture(() =>
    service.buildOnboardingModelInput({ ...formValues, max_tokens: 0 }, credential)
  )
};

observations.requestBodies = observations.successRequests.map((entry) => entry.body);
observations.modelBodyKeys = Object.keys(observations.modelInput).sort();
observations.memberBodyKeys = Object.keys(observations.memberInput).sort();
observations.canaryInModelBody = JSON.stringify(observations.modelInput).includes('sk-canary');
observations.canaryInMemberBody = JSON.stringify(observations.memberInput).includes('sk-canary');

console.log(JSON.stringify(observations));
"""

REQUIRED_KEYS = (
    "model.flowHint",
    "model.create.title",
    "model.create.step.key",
    "model.create.step.model",
    "model.create.mode.existing",
    "model.create.mode.new",
    "model.create.selectKey",
    "model.create.selectKeyRequired",
    "model.create.noEligibleKeys",
    "model.create.credentialsFailed",
    "model.create.credentialsTruncated",
    "model.create.newKeyHint",
    "model.create.providerLockedHint",
    "model.create.next",
    "model.create.back",
    "model.create.submit",
    "model.create.retryBind",
    "model.create.bindingFailed",
    "model.create.created",
    "model.create.goMembers",
)


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_service_harness(tmp_path: Path) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "entry.ts").write_text(
        ENTRY.format(service=SERVICE_TS, client=CLIENT_TS), encoding="utf-8"
    )
    (tmp_path / "semi-stub.mjs").write_text(
        "export const Toast = { error() {}, success() {}, info() {}, warning() {} };\n",
        encoding="utf-8",
    )
    harness = HARNESS_JS.replace("__MODEL_ID__", MODEL_ID).replace(
        "__CREDENTIAL_ID__", CREDENTIAL_ID
    )
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'onboarding.mjs'}",
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
def onboarding_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("model-onboarding"))


def test_real_service_creates_model_then_binds_first_member(
    onboarding_service: dict[str, Any],
) -> None:
    """先 POST /models，再 POST /models/{id}/deployments；成员只引用凭据，不复制容量。"""
    success = onboarding_service["success"]
    assert success["model"]["id"] == MODEL_ID
    assert success["member"]["key"] == "deepseek-main"
    assert success["member"]["credential_id"] == CREDENTIAL_ID

    requests = onboarding_service["successRequests"]
    assert [entry["method"] for entry in requests] == ["POST", "POST"]
    assert requests[0]["url"] == "/models"
    assert requests[1]["url"] == f"/models/{MODEL_ID}/deployments"

    model_body = requests[0]["body"]
    assert model_body["provider_key"] == "deepseek", "供方必须取所选物理 Key"
    assert model_body["params"] == {"max_tokens": 4096}
    assert "base_url" not in model_body and "api_key" not in model_body

    member_body = requests[1]["body"]
    assert member_body == {
        "key": "deepseek-main",
        "credential_id": CREDENTIAL_ID,
        "weight": 1,
        "enabled": True,
    }
    assert "api_key" not in member_body and "base_url" not in member_body


def test_real_service_keeps_the_created_model_when_binding_fails(
    onboarding_service: dict[str, Any],
) -> None:
    """绑定失败不能吞掉已创建的模型：错误携带模型与原错误码，调用方可重试/去成员页。"""
    failure = onboarding_service["bindingFailure"]
    assert failure == {
        "name": "MemberBindingFailedError",
        "modelId": MODEL_ID,
        "reasonCode": "MODEL_PROVIDER_MISMATCH",
    }
    requests = onboarding_service["bindingFailureRequests"]
    assert [entry["url"] for entry in requests] == [
        "/models",
        f"/models/{MODEL_ID}/deployments",
    ]


def test_real_service_treats_an_existing_member_as_bound(
    onboarding_service: dict[str, Any],
) -> None:
    """响应丢失后的重试命中 MODEL_DEPLOYMENT_EXISTS：说明成员已经落库，不应再报失败。"""
    assert onboarding_service["alreadyBound"] is True
    assert onboarding_service["retryRequests"][0]["url"] == (
        f"/models/{MODEL_ID}/deployments"
    )


def test_real_service_builders_force_the_credential_provider(
    onboarding_service: dict[str, Any],
) -> None:
    """表单里的 provider 不参与：模型体取物理 Key 的供方；成员体恰为四个契约字段。"""
    assert onboarding_service["modelInput"]["provider_key"] == "deepseek"
    assert onboarding_service["modelBodyKeys"] == [
        "enabled",
        "key",
        "model_id",
        "name",
        "params",
        "protocol",
        "provider_key",
    ]
    assert onboarding_service["memberInput"] == {
        "key": "deepseek-main",
        "credential_id": CREDENTIAL_ID,
        "weight": 1,
        "enabled": True,
    }
    assert onboarding_service["constructorErrors"]["blankKey"]["reason"] == "KEY_REQUIRED"
    assert onboarding_service["constructorErrors"]["zeroBudget"]["reason"] == (
        "MAX_TOKENS_INVALID"
    )
    assert onboarding_service["canaryInModelBody"] is False
    assert onboarding_service["canaryInMemberBody"] is False


def test_wizard_source_implements_two_steps_and_auto_binding() -> None:
    """向导两步：物理 Key → 模型信息；保存时自动绑定；绑定失败给出重试/去成员页。"""
    source = _read(WIZARD_TSX)
    assert "Steps" in source and source.count("<Steps.Step") == 2
    assert "createCredential" in source and "createModelWithMember" in source
    assert "bindFirstMember" in source and "isMemberAlreadyBound" in source
    assert "providerLocked" in source and "model.create.providerLockedHint" in source
    assert 'data-testid="create-model-wizard"' in source
    assert 'data-testid="model-wizard-next"' in source
    assert 'data-testid="model-wizard-submit"' in source
    # 两步表单必须是两个独立实例：同位置复用同一个 Form 会让第二步的 FormApi 永不注册
    assert 'key="credential-step"' in source and 'key="model-step"' in source
    assert "model.create.next" in source and "model.create.submit" in source
    assert "model.create.retryBind" in source and "model.create.goMembers" in source
    assert "RadioGroup" in source and "model.create.mode.existing" in source
    assert "model.create.mode.new" in source
    assert "ModelFields" in source and "CredentialFields" in source


def test_page_mounts_the_wizard_for_admin_and_keeps_edit_modal() -> None:
    """ADMIN 的新增入口打开向导；编辑仍走原弹窗；资源准备按钮在新增之前。"""
    page = _read(PAGE_TSX)
    assert "ModelCreateWizard" in page and "<ModelCreateWizard" in page
    assert "isAdmin" in page and "setCreateVisible(true)" in page
    assert "setFormModel(null)" in page and "setFormVisible(true)" in page, (
        "非 ADMIN 仍保留原新增/编辑路径（资源池仅 ADMIN）"
    )
    assert page.index('data-testid="open-model-resources"') < page.index(
        'data-testid="create-model"'
    ), "资源准备必须先于新增模型出现"
    assert "model.flowHint" in page, "列表上方必须有流程提示"
    assert "initialTab={detailTab}" in page, "向导成功后直接落到成员页签"
    assert "setDetailTab('members')" in page


def test_wizard_and_field_components_compile(tmp_path: Path) -> None:
    """真实 TSX 编译产物：两步向导、共享字段面与自动绑定链路都在模块图里。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    wizard = _compile_tsx(WIZARD_TSX, tmp_path / "wizard.mjs")
    assert '"create-model-wizard"' in wizard
    assert "createModelWithMember" in wizard and "bindFirstMember" in wizard
    assert "createCredential" in wizard
    assert "ModelFields" in wizard and "CredentialFields" in wizard

    model_fields = _compile_tsx(MODEL_FIELDS_TSX, tmp_path / "model-fields.mjs")
    assert "max_tokens" in model_fields and "provider_key" in model_fields
    assert "base_url" not in model_fields and "api_key" not in model_fields

    credential_fields = _compile_tsx(CREDENTIAL_FIELDS_TSX, tmp_path / "credential-fields.mjs")
    assert "base_url" in credential_fields and "api_key" in credential_fields
    assert "max_inflight" in credential_fields


def test_wizard_keys_are_localized() -> None:
    """向导与流程提示的 zh/en 词条齐平，且不是裸字段名。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in ("model.create.title", "model.create.next", "model.flowHint"):
        assert zh[key] != en[key], f"{key} 未本地化"
