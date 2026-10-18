"""[B-288] 额度组管理与共享预算界面（design frontend §3.2/§3.4/§3.7；backend API-02）。

真实边界：真实模块 service（`modules/model-management/services/modelQuotas.ts`）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器）、仓库内 esbuild 编译产物与真实 node 执行；纯解析/状态
（CRUD 请求体、CAS 版本、null 维度语义、输入边界、错误码映射）跑真实代码，DOM 交互留真实浏览器
E2E（TASK-115）。断言覆盖：

- **ADMIN 维护面 CRUD**：列表 / 创建（`Idempotency-Key` + 共享传输 CSRF）/ 整份替换
  （CAS `expected_revision`）/ 删除，全部走 `/api/v1/model-quota-groups`（API-02）；
- **null 维度语义**：`rpm`/`tpm` 为 `null` = 该维度**不设应用内限流**（不是零额度），
  读投影与展示视图都不折叠成 `0`；
- **共享预算说明**：多个物理 Key/模型成员引用同一额度组时共享这一份容量、不按 Key 数复制；
  组外 Key 独占自身容量；
- **冲突显式**：降额/新开限流需排空（`MODEL_CAPACITY_DRAIN_REQUIRED`）、引用中删除
  （`MODEL_QUOTA_GROUP_IN_USE`）、同名与版本冲突各有本地化文案；删除失败不误清本地列表；
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
SERVICE_TS = MODULE / "services/modelQuotas.ts"
PANEL_TSX = MODULE / "ModelQuotaPanel.tsx"
CLIENT_TS = SRC / "api/client.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

GROUP_ID = "22222222-2222-4222-8222-222222222222"

#: 新词条：额度组维护面与共享预算说明的全部文案（两侧必须齐平，见 test_quota_keys_are_localized）。
REQUIRED_KEYS = (
    "model.quota.title",
    "model.quota.subtitle",
    "model.quota.add",
    "model.quota.empty",
    "model.quota.truncated",
    "model.quota.columns.key",
    "model.quota.columns.maxInflight",
    "model.quota.columns.rpm",
    "model.quota.columns.tpm",
    "model.quota.columns.revision",
    "model.quota.columns.actions",
    "model.quota.actions.edit",
    "model.quota.actions.delete",
    "model.quota.confirmDelete",
    "model.quota.unit.maxInflight",
    "model.quota.unit.rpm",
    "model.quota.unit.tpm",
    "model.quota.limit.value",
    "model.quota.limit.unlimited",
    "model.quota.limit.unlimitedHint",
    "model.quota.shareHint",
    "model.quota.drainHint",
    "model.quota.deleteHint",
    "model.quota.error.exists",
    "model.quota.error.drainRequired",
    "model.quota.error.inUse",
    "model.quota.error.revisionConflict",
    "model.quota.form.createTitle",
    "model.quota.form.editTitle",
    "model.quota.form.key",
    "model.quota.form.keyHint",
    "model.quota.form.keyRequired",
    "model.quota.form.maxInflight",
    "model.quota.form.maxInflightHint",
    "model.quota.form.maxInflightInvalid",
    "model.quota.form.rpm",
    "model.quota.form.rpmHint",
    "model.quota.form.rpmInvalid",
    "model.quota.form.tpm",
    "model.quota.form.tpmHint",
    "model.quota.form.tpmInvalid",
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

const GROUP_ID = '__GROUP_ID__';

const mod = await import('./quotas.mjs');
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

// 共享额度组：RPM 不设限（null）、TPM 有值、在飞上限有值
const group = {
  id: GROUP_ID,
  key: 'deepseek-shared',
  max_inflight: 16,
  rpm: null,
  tpm: 1200,
  revision: 2
};

const formValues = {
  key: 'deepseek-shared',
  max_inflight: 16,
  rpm: 600,
  tpm: null
};

const observations = {};

// 1) 列表读取：分页参数与读取投影（null 维度原样保留）
payload = { items: [group], page: 1, page_size: 20, total: 1 };
observations.listResult = await service.listQuotaGroups({ page: 1, page_size: 20 });
observations.listRequest = observed();

// 2) 创建：幂等键由调用方持有并透传；创建体不含 revision
payload = group;
const createInput = service.buildQuotaGroupCreateInput(formValues);
observations.createInput = createInput;
observations.createResult = await service.createQuotaGroup(createInput, 'idem-288');
observations.createRequest = observed();

// 3) 整份替换 + CAS 版本（放宽 TPM：不触发排空）
payload = { ...group, revision: 3, rpm: 600 };
const updateInput = service.buildQuotaGroupUpdateInput(group, formValues);
observations.updateInput = updateInput;
observations.updateResult = await service.updateQuotaGroup(GROUP_ID, updateInput);
observations.updateRequest = observed();

// 4) 删除
payload = { id: GROUP_ID, deleted: true };
await service.deleteQuotaGroup(GROUP_ID);
observations.deleteRequest = observed();

// 5) null 维度语义：null = 不设应用内限流，不折叠成 0
observations.limits = {
  rpmState: service.quotaLimitState(group.rpm),
  tpmState: service.quotaLimitState(group.tpm),
  maxInflightState: service.quotaLimitState(group.max_inflight),
  zeroState: service.quotaLimitState(0)
};
observations.descriptors = service.describeQuotaGroup(group);

// 6) 纯映射：输入边界显式失败
observations.builders = {
  blankKey: captureError(() => service.buildQuotaGroupCreateInput({ ...formValues, key: '   ' })),
  zeroInflight: captureError(() =>
    service.buildQuotaGroupCreateInput({ ...formValues, max_inflight: 0 })
  ),
  fractionalInflight: captureError(() =>
    service.buildQuotaGroupCreateInput({ ...formValues, max_inflight: 2.5 })
  ),
  zeroRpm: captureError(() => service.buildQuotaGroupCreateInput({ ...formValues, rpm: 0 })),
  fractionalTpm: captureError(() => service.buildQuotaGroupCreateInput({ ...formValues, tpm: 1.5 }))
};
observations.unlimitedInput = service.buildQuotaGroupCreateInput({
  ...formValues,
  rpm: null,
  tpm: null
});

// 7) 冲突码 → 本地化文案键
observations.errors = {
  exists: service.quotaGroupSaveErrorKey({
    response: { data: { code: 'MODEL_QUOTA_GROUP_EXISTS', msg: '已存在' } }
  }),
  drain: service.quotaGroupSaveErrorKey({
    response: { data: { code: 'MODEL_CAPACITY_DRAIN_REQUIRED', msg: '需排空' } }
  }),
  inUse: service.quotaGroupSaveErrorKey({ code: 'MODEL_QUOTA_GROUP_IN_USE' }),
  revision: service.quotaGroupSaveErrorKey({
    response: { data: { code: 'REVISION_CONFLICT', msg: '版本冲突' } }
  }),
  unknown: service.quotaGroupSaveErrorKey(new Error('boom'))
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
    (tmp_path / "harness.mjs").write_text(
        HARNESS_JS.replace("__GROUP_ID__", GROUP_ID), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'quotas.mjs'}",
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
def quota_service(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("quota-ui"))


def test_real_service_maps_the_quota_group_routes(quota_service: dict[str, Any]) -> None:
    """API-02 维护面：列表/创建/整份替换/删除，写操作带幂等键、CSRF 与请求追踪 ID。"""
    listing = quota_service["listRequest"]
    assert listing["method"] == "GET"
    assert listing["url"] == "/model-quota-groups"
    assert listing["params"] == {"page": 1, "page_size": 20}
    assert listing["body"] is None
    assert quota_service["listResult"]["total"] == 1

    created = quota_service["createRequest"]
    assert created["method"] == "POST"
    assert created["url"] == "/model-quota-groups"
    assert created["idempotencyKey"] == "idem-288", "创建必须透传调用方持有的幂等键"
    assert created["csrf"] == "console-csrf-value", "写操作走共享传输的 CSRF 头"
    assert created["requestId"], "写操作仍走共享传输的 X-Request-Id"
    assert set(created["body"]) == {"key", "max_inflight", "rpm", "tpm"}, (
        "创建体只能是契约字段：不携带 revision，也不夹带凭据/密钥字段"
    )
    assert created["body"]["rpm"] == 600

    updated = quota_service["updateRequest"]
    assert updated["method"] == "PUT"
    assert updated["url"] == f"/model-quota-groups/{GROUP_ID}"
    assert set(updated["body"]) == {"key", "max_inflight", "rpm", "tpm", "expected_revision"}, (
        "整份替换：可编辑字段 + CAS 版本，不能只发改动字段"
    )
    assert updated["body"]["expected_revision"] == 2, "整份替换必须带 CAS 版本"
    assert updated["body"]["tpm"] is None, "留空维度以 null 提交（不设限），不是 0"

    deleted = quota_service["deleteRequest"]
    assert deleted["method"] == "DELETE"
    assert deleted["url"] == f"/model-quota-groups/{GROUP_ID}"


def test_real_service_keeps_null_dimensions_meaning_unlimited(quota_service: dict[str, Any]) -> None:
    """null = 该维度不设应用内限流（不是零额度）：读投影与展示视图都不折叠成 0。"""
    assert quota_service["listResult"]["items"][0]["rpm"] is None, "读投影的 null 必须原样保留"
    assert quota_service["listResult"]["items"][0]["tpm"] == 1200

    limits = quota_service["limits"]
    assert limits == {
        "rpmState": "UNLIMITED",
        "tpmState": "LIMITED",
        "maxInflightState": "LIMITED",
        "zeroState": "LIMITED",
    }, "只有 null 才是「不设限」：0 是数值（且不是合法额度），不能与 null 混为一谈"

    descriptors = quota_service["descriptors"]
    assert descriptors["rpm"] == {
        "value": None,
        "state": "UNLIMITED",
        "unitKey": "model.quota.unit.rpm",
    }
    assert descriptors["tpm"] == {
        "value": 1200,
        "state": "LIMITED",
        "unitKey": "model.quota.unit.tpm",
    }
    assert descriptors["maxInflight"]["value"] == 16
    assert descriptors["maxInflight"]["unitKey"] == "model.quota.unit.maxInflight"
    assert quota_service["updateInput"]["tpm"] is None


def test_real_service_validates_the_quota_input(quota_service: dict[str, Any]) -> None:
    """输入边界显式失败：标识必填；在飞上限/RPM/TPM 必须是正整数，留空 = 不设限。"""
    builders = quota_service["builders"]
    assert builders["blankKey"] == "KEY_REQUIRED"
    assert builders["zeroInflight"] == "MAX_INFLIGHT_INVALID"
    assert builders["fractionalInflight"] == "MAX_INFLIGHT_INVALID"
    assert builders["zeroRpm"] == "RPM_INVALID"
    assert builders["fractionalTpm"] == "TPM_INVALID"
    assert quota_service["unlimitedInput"] == {
        "key": "deepseek-shared",
        "max_inflight": 16,
        "rpm": None,
        "tpm": None,
    }, "留空维度必须是显式 null，不能落成 0 或省略键"
    assert quota_service["createInput"]["max_inflight"] == 16


def test_real_service_maps_conflicts_to_explicit_copy(quota_service: dict[str, Any]) -> None:
    """同名、降额排空、引用中删除与版本冲突各有明确文案键；未知失败回落通用文案。"""
    assert quota_service["errors"] == {
        "exists": "model.quota.error.exists",
        "drain": "model.quota.error.drainRequired",
        "inUse": "model.quota.error.inUse",
        "revision": "model.quota.error.revisionConflict",
        "unknown": "common.saveFailed",
    }


def test_panel_is_admin_gated_and_keeps_the_referenced_group() -> None:
    """[RULE-front-001] 维护入口仅 ADMIN；删除冲突只提示，不误清仍被引用的额度。"""
    source = _read(PANEL_TSX)
    assert "useAuth" in source
    assert "account?.role === 'ADMIN'" in source
    assert re.search(r"if\(!isAdmin\)\{returnnull;?\}", _compact(source)), (
        "非 ADMIN 必须直接不渲染维护面（不是只跳过请求）"
    )
    compact = _compact(source)
    assert "quotaGroupSaveErrorKey(error)" in compact, "冲突码只经 service 的纯映射函数"
    assert "error.response" not in source, "组件不得手搓错误码提取（只走 apiErrorBody 的封装）"
    assert re.search(
        r"catch\(error\)\{setActionErrorKey\(quotaGroupSaveErrorKey\(error\)\);return;\}afterMutation\(\);",
        compact,
    ), "删除失败必须提前返回：不得刷新/清空列表，仍被引用的额度组保持可见"


def test_panel_states_units_sharing_and_null_dimension_semantics() -> None:
    """RPM/TPM/null/max_inflight 单位与共享关系可见；降额排空与删除边界有常驻说明。"""
    source = _read(PANEL_TSX)
    assert "describeQuotaGroup" in source, "单位与 null 维度语义只经 service 的展示视图"
    assert "model.quota.limit.unlimited" in source, "null 维度必须显示为「不设限」而不是 0"
    assert "model.quota.limit.unlimitedHint" in source, "必须说明「不设限」不等于零额度"
    assert "model.quota.shareHint" in source, "必须说明额度组是共享预算：不按 Key 数复制额度"
    assert "model.quota.drainHint" in source, "必须说明降额/新开限流需要先排空"
    assert "model.quota.deleteHint" in source, "必须说明仍被引用时删除会冲突、不误清引用"
    assert "model.quota.unit.rpm" in source or "unitKey" in source, "维度单位必须来自词条"
    for leaked in ("api_key", "credential_id", "provider_key"):
        assert leaked not in source, f"额度组维护面不得出现凭据字段：{leaked}"
    assert "createQuotaGroup(" in source and "idempotencyKey" in source, (
        "创建必须透传调用方持有的幂等键"
    )


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
        "components/common/TableText",
    ):
        assert component in source, f"缺少共享组件：{component}"
    assert "requestSeq" in source and "seq !== requestSeq.current" in source, (
        "列表加载必须有请求竞态守卫"
    )
    assert 'data-testid="quota-panel"' in source

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
    assert "quota-panel" in artifact, "编译产物必须包含面板本身（不是空转）"
    assert "model.quota.limit.unlimited" in artifact


def test_quota_keys_are_localized() -> None:
    """额度组词条 zh-CN 与 en-US 齐平；单位与「不设限」不得是裸枚举码。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in (
        "model.quota.unit.maxInflight",
        "model.quota.unit.rpm",
        "model.quota.unit.tpm",
        "model.quota.limit.unlimited",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
        assert zh[key] != key.rsplit(".", 1)[-1], f"{key} 是裸枚举码"
    assert "不是零额度" in zh["model.quota.limit.unlimitedHint"], (
        "null 维度的说明必须点明「不设限 ≠ 零额度」"
    )
    assert "共享" in zh["model.quota.shareHint"] and "复制" in zh["model.quota.shareHint"], (
        "共享预算说明必须点明组内共享、不按 Key 数复制"
    )
