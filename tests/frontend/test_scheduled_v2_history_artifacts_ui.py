"""TASK-027：产物版本下载与失败分型（design frontend §3.4、§3.6、§3.9 API-25）。

真实边界：真实产物下载控制器（`task-center/artifactDownload.ts`）与真实任务中心 typed
service / 共享传输经 esbuild 编译由真实 node 执行；`TaskArtifactList` 经真实 React SSR +
真实 Semi + 真实 i18n 渲染。

断言覆盖：

- **精确版本**：请求只带 `artifact_id` 在路径、`version + checksum` 在查询参数，不退最新版。
- **Blob URL 不泄漏**：创建一次、触发一次下载、随即释放同一个 URL；点击抛错也释放。
- **失败即失败**：版本冲突/不存在/网络/未知码各有明确文案，没有 URL、没有「已保存」。
- **在途单飞与重置栅栏**：重复点击只发一次；身份重置后迟到的响应不复活状态。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/webchat"
TASK_CENTER = MODULE / "task-center"
DOWNLOAD_TS = TASK_CENTER / "artifactDownload.ts"
ARTIFACT_TSX = TASK_CENTER / "TaskArtifactList.tsx"
TASK_CENTER_TS = MODULE / "services/taskCenterApi.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

ARTIFACT_1 = "77777777-7777-4777-8777-777777777771"
ARTIFACT_2 = "77777777-7777-4777-8777-777777777772"
CHECKSUM = "sha256:" + "c" * 64
CHECKSUM_2 = "sha256:" + "d" * 64
VERSION = "v3"
NOW_ISO = "2026-11-17T01:00:00Z"


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(tmp_path: Path, entry: Path, outfile: str, with_stub: bool) -> None:
    command = [
        str(ESBUILD),
        str(entry),
        "--bundle",
        "--format=esm",
        "--platform=node",
        f"--outfile={tmp_path / outfile}",
        "--log-level=warning",
        "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
    ]
    if with_stub:
        command.append(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}")
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"


SEMI_STUB = """export const Button = () => null;
export const Typography = Object.assign(() => null, { Title: () => null, Text: () => null });
export const Toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
export const Avatar = () => null;
export const Banner = () => null;
export const Card = () => null;
export const Collapsible = () => null;
export const ConfigProvider = () => null;
export const DatePicker = () => null;
export const Descriptions = () => null;
export const Dropdown = () => null;
export const Empty = () => null;
export const Form = () => null;
export const Input = () => null;
export const InputNumber = () => null;
export const Layout = () => null;
export const MarkdownRender = () => null;
export const Modal = () => null;
export const Nav = () => null;
export const Pagination = () => null;
export const Popconfirm = () => null;
export const Popover = () => null;
export const Radio = () => null;
export const RadioGroup = () => null;
export const Select = () => null;
export const SideSheet = () => null;
export const Skeleton = () => null;
export const Spin = () => null;
export const Steps = () => null;
export const Switch = () => null;
export const Table = () => null;
export const TabPane = () => null;
export const Tabs = () => null;
export const Tag = () => null;
export const TextArea = () => null;
export const Timeline = () => null;
export const Tooltip = () => null;
export const Upload = () => null;
"""

LOGIC_ENTRY = """export {
  createArtifactDownloadController,
  artifactErrorKey,
  artifactErrorNeedsCode,
  IDLE_ARTIFACT_DOWNLOAD
} from '__DOWNLOAD__';
export { api } from '__CLIENT__';
export { setChatContextId } from '__AUTH_SCOPE__';
export { createTaskCenterClient } from '__TASK_CENTER__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-909' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };

const ARTIFACT_1 = '__ARTIFACT_1__';
const ARTIFACT_2 = '__ARTIFACT_2__';
const CHECKSUM = '__CHECKSUM__';
const CHECKSUM_2 = '__CHECKSUM_2__';
const VERSION = '__VERSION__';
const NOW_ISO = '__NOW_ISO__';

const mod = await import('./logic.mjs');
const {
  api,
  createTaskCenterClient,
  createArtifactDownloadController,
  artifactErrorKey,
  artifactErrorNeedsCode,
  IDLE_ARTIFACT_DOWNLOAD,
  setChatContextId
} = mod;

setChatContextId('88888888-8888-4888-8888-888888888888');

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
const artifact = (overrides = {}) => ({
  artifact_id: ARTIFACT_1,
  name: 'weekly.md',
  media_type: 'text/markdown',
  size: 2048,
  checksum: CHECKSUM,
  version: VERSION,
  ...overrides
});
const env = () => {
  const state = { created: [], revoked: [], targets: [], clicks: 0, throwOnClick: false };
  return {
    state,
    environment: {
      scope: {
        create: (blob) => {
          const url = 'blob:task-' + (state.created.length + 1);
          state.created.push({ url, size: blob?.size ?? null });
          return url;
        },
        revoke: (url) => state.revoked.push(url)
      },
      target: (url, filename) => ({
        href: url,
        download: filename,
        click: () => {
          state.clicks += 1;
          state.targets.push({ url, filename });
          if (state.throwOnClick) throw new Error('click blocked');
        }
      })
    }
  };
};

const envelope = (data) => ({
  code: '0',
  msg: 'ok',
  data,
  trace_id: 't',
  request_id: 'r',
  timestamp: NOW_ISO
});
const blobOk = (marker) =>
  Promise.resolve({
    data: new Blob([marker], { type: 'text/markdown' }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {}
  });
const fail = (status, code) => {
  const error = new Error(code);
  error.isAxiosError = true;
  error.config = {};
  error.request = {};
  error.response = { status, data: { code, msg: code }, headers: {}, config: {} };
  return Promise.reject(error);
};
const transportFailure = () => {
  const error = new Error('network down');
  error.isAxiosError = true;
  error.config = {};
  error.request = {};
  return Promise.reject(error);
};
const deferred = () => {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

const http = { requests: [], handler: null };
api.defaults.adapter = (config) => {
  http.requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    responseType: config.responseType ?? null
  });
  return http.handler(config);
};
const client = createTaskCenterClient();
const controllerWith = (state) =>
  createArtifactDownloadController({ port: client, environment: state.environment });
const observations = {};

// 1) 构造不发请求。
{
  http.requests = [];
  http.handler = () => fail(500, 'UNROUTED');
  const { state, environment } = env();
  createArtifactDownloadController({ port: client, environment });
  await settle(40);
  observations.construct = { requests: http.requests.length };
}

// 2) 成功：精确 id/version/checksum；URL 创建一次、释放一次。
{
  http.requests = [];
  http.handler = () => blobOk('PAYLOAD');
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(30);
  observations.success = {
    requests: http.requests.slice(),
    entry: controller.entry(ARTIFACT_1),
    env: state
  };
  controller.dispose();
}

// 3) 版本/校验冲突：明确失败，不创建 URL、不冒充成功。
{
  http.requests = [];
  http.handler = () => fail(409, 'ARTIFACT_VERSION_CONFLICT');
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(30);
  const entry = controller.entry(ARTIFACT_1);
  observations.versionConflict = {
    entry,
    key: artifactErrorKey(entry.errorCode),
    needsCode: artifactErrorNeedsCode(entry.errorCode),
    env: state,
    requests: http.requests.slice()
  };
  controller.dispose();
}

// 4) 传输失败与未知码分别归类。
{
  http.requests = [];
  http.handler = () => transportFailure();
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(30);
  const network = controller.entry(ARTIFACT_1);
  observations.network = { entry: network, key: artifactErrorKey(network.errorCode), env: state };
  controller.dispose();
}
{
  http.requests = [];
  http.handler = () => fail(400, 'WEIRD_CODE');
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(30);
  const unknown = controller.entry(ARTIFACT_1);
  observations.unknownCode = {
    entry: unknown,
    key: artifactErrorKey(unknown.errorCode),
    needsCode: artifactErrorNeedsCode(unknown.errorCode),
    env: state
  };
  controller.dispose();
}

// 5) 在途单飞：重复点击只发一次请求。
{
  http.requests = [];
  const slot = deferred();
  http.handler = () => slot.promise;
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  controller.download(artifact());
  await settle(20);
  observations.singleFlight = {
    requests: http.requests.length,
    phase: controller.entry(ARTIFACT_1).phase
  };
  slot.resolve({
    data: new Blob(['PAYLOAD'], { type: 'text/markdown' }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {}
  });
  await settle(30);
  observations.singleFlight.after = controller.entry(ARTIFACT_1).phase;
  observations.singleFlight.env = state;
  controller.dispose();
}

// 6) 失败后可重试，参数与第一次完全相同。
{
  http.requests = [];
  let calls = 0;
  http.handler = () => {
    calls += 1;
    return calls === 1 ? fail(409, 'ARTIFACT_VERSION_CONFLICT') : blobOk('PAYLOAD');
  };
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(30);
  controller.download(artifact());
  await settle(30);
  observations.retry = {
    entry: controller.entry(ARTIFACT_1),
    requests: http.requests.slice(),
    env: state
  };
  controller.dispose();
}

// 7) 身份重置：迟到的成功响应不得复活状态，也不创建 URL。
{
  http.requests = [];
  const slot = deferred();
  http.handler = () => slot.promise;
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(20);
  controller.reset();
  slot.resolve({
    data: new Blob(['PAYLOAD'], { type: 'text/markdown' }),
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {}
  });
  await settle(40);
  observations.resetFence = { entry: controller.entry(ARTIFACT_1), env: state };
  controller.dispose();
}

// 8) 保存阶段抛错：URL 仍然释放，状态是失败而不是已保存。
{
  http.requests = [];
  http.handler = () => blobOk('PAYLOAD');
  const { state, environment } = env();
  state.throwOnClick = true;
  const controller = controllerWith({ environment });
  controller.download(artifact());
  await settle(40);
  observations.saveFailure = {
    entry: controller.entry(ARTIFACT_1),
    key: artifactErrorKey(controller.entry(ARTIFACT_1).errorCode),
    env: state
  };
  controller.dispose();
}

// 9) 空 artifact_id 不发请求（缺参在本地失败，不用 4xx 表达）。
{
  http.requests = [];
  http.handler = () => blobOk('PAYLOAD');
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact({ artifact_id: '   ' }));
  await settle(20);
  observations.blankId = { requests: http.requests.length, entry: controller.entry('   ') };
  controller.dispose();
}

// 10) 两个产物各自精确绑定自己的 version/checksum。
{
  http.requests = [];
  http.handler = () => blobOk('PAYLOAD');
  const { state, environment } = env();
  const controller = controllerWith({ environment });
  controller.download(artifact());
  controller.download(artifact({ artifact_id: ARTIFACT_2, checksum: CHECKSUM_2, version: 'v9' }));
  await settle(60);
  observations.twoArtifacts = {
    requests: http.requests.slice(),
    env: state,
    entries: [controller.entry(ARTIFACT_1), controller.entry(ARTIFACT_2)]
  };
  controller.dispose();
}

console.log(JSON.stringify(observations, (key, value) => (value instanceof Blob ? '[blob]' : value)));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { TaskArtifactList } from '__ARTIFACTS__';
import zh from '__ZH__';
import en from '__EN__';

const ARTIFACT_1 = '__ARTIFACT_1__';
const ARTIFACT_2 = '__ARTIFACT_2__';
const CHECKSUM = '__CHECKSUM__';
const CHECKSUM_2 = '__CHECKSUM_2__';
const VERSION = '__VERSION__';

const artifacts = [
  {
    artifact_id: ARTIFACT_1,
    name: 'weekly.md',
    media_type: 'text/markdown',
    size: 2048,
    checksum: CHECKSUM,
    version: VERSION
  },
  {
    artifact_id: ARTIFACT_2,
    name: 'chart.png',
    media_type: 'image/png',
    size: 1048576,
    checksum: CHECKSUM_2,
    version: 'v9'
  }
];

async function i18nFor(locale) {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({
    lng: locale,
    fallbackLng: 'zh-CN',
    resources: { 'zh-CN': { translation: zh }, 'en-US': { translation: en } },
    interpolation: { escapeValue: false }
  });
  return i18n;
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const node = React.createElement(TaskArtifactList, {
      artifacts: scenario.empty ? [] : artifacts,
      state: { entries: scenario.entries ?? {} },
      onDownload: () => {}
    });
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, node)
    );
  }
  return { markup };
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: '' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };
const mod = await import('./render.mjs');
const scenarios = JSON.parse(
  await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8')
);
console.log(JSON.stringify(await mod.renderScenarios(scenarios)));
"""

SCENARIOS = (
    {"id": "saved-zh", "locale": "zh-CN",
     "entries": {ARTIFACT_1: {"phase": "SAVED", "errorCode": None}}},
    {"id": "downloading-zh", "locale": "zh-CN",
     "entries": {ARTIFACT_1: {"phase": "DOWNLOADING", "errorCode": None}}},
    {"id": "failed-conflict-zh", "locale": "zh-CN",
     "entries": {ARTIFACT_1: {"phase": "FAILED", "errorCode": "ARTIFACT_VERSION_CONFLICT"}}},
    {"id": "failed-unknown-zh", "locale": "zh-CN",
     "entries": {ARTIFACT_1: {"phase": "FAILED", "errorCode": "WEIRD_CODE"}}},
    {"id": "failed-en", "locale": "en-US",
     "entries": {ARTIFACT_1: {"phase": "FAILED", "errorCode": "TASK_ARTIFACT_NOT_FOUND"}}},
    {"id": "empty-zh", "locale": "zh-CN", "empty": True},
)

PLACEHOLDERS = (
    ("__ARTIFACT_1__", ARTIFACT_1),
    ("__ARTIFACT_2__", ARTIFACT_2),
    ("__CHECKSUM__", CHECKSUM),
    ("__CHECKSUM_2__", CHECKSUM_2),
    ("__VERSION__", VERSION),
    ("__NOW_ISO__", NOW_ISO),
)


def _fill(source: str) -> str:
    for placeholder, value in PLACEHOLDERS:
        source = source.replace(placeholder, value)
    return source


@pytest.fixture(scope="module")
def artifact_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("artifact-logic")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        _fill(
            LOGIC_ENTRY.replace("__DOWNLOAD__", str(DOWNLOAD_TS))
            .replace("__TASK_CENTER__", str(TASK_CENTER_TS))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "logic.mjs", with_stub=True)
    (tmp_path / "harness.mjs").write_text(_fill(LOGIC_HARNESS), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def artifact_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("artifact-render")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__ARTIFACTS__", str(ARTIFACT_TSX))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json"))
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "render.mjs", with_stub=False)
    (tmp_path / "scenarios.json").write_text(
        json.dumps(SCENARIOS, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(RENDER_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_controller_is_read_only_until_clicked(artifact_logic: dict[str, Any]) -> None:
    assert artifact_logic["construct"]["requests"] == 0


def test_download_binds_the_exact_version_and_revokes_the_url(
    artifact_logic: dict[str, Any],
) -> None:
    success = artifact_logic["success"]
    assert len(success["requests"]) == 1
    request = success["requests"][0]
    assert (request["method"], request["url"]) == (
        "GET",
        f"/webchat/task-center/artifacts/{ARTIFACT_1}",
    )
    assert request["params"] == {"version": VERSION, "checksum": CHECKSUM}
    assert request["responseType"] == "blob"
    assert success["entry"] == {"phase": "SAVED", "errorCode": None}
    env = success["env"]
    assert [item["url"] for item in env["created"]] == ["blob:task-1"]
    assert env["revoked"] == ["blob:task-1"], "Blob URL 创建后必须释放"
    assert env["targets"] == [{"url": "blob:task-1", "filename": "weekly.md"}]
    assert env["clicks"] == 1


def test_version_conflict_is_an_explicit_failure(artifact_logic: dict[str, Any]) -> None:
    conflict = artifact_logic["versionConflict"]
    assert conflict["entry"] == {"phase": "FAILED", "errorCode": "ARTIFACT_VERSION_CONFLICT"}
    assert conflict["key"] == "chat.taskCenter.artifact.error.versionConflict"
    assert conflict["needsCode"] is False
    assert conflict["env"]["created"] == [], "失败不得创建下载 URL"
    assert conflict["env"]["clicks"] == 0
    assert len(conflict["requests"]) == 1


def test_failure_classes_map_to_the_catalog(artifact_logic: dict[str, Any]) -> None:
    network = artifact_logic["network"]
    assert network["entry"]["errorCode"] == "NETWORK_ERROR"
    assert network["key"] == "chat.taskCenter.artifact.error.network"
    assert network["env"]["created"] == []

    unknown = artifact_logic["unknownCode"]
    assert unknown["entry"]["errorCode"] == "WEIRD_CODE"
    assert unknown["key"] == "chat.taskCenter.artifact.error.failed"
    assert unknown["needsCode"] is True
    assert unknown["env"]["created"] == []


def test_duplicate_clicks_are_single_flight(artifact_logic: dict[str, Any]) -> None:
    single = artifact_logic["singleFlight"]
    assert single["requests"] == 1
    assert single["phase"] == "DOWNLOADING"
    assert single["after"] == "SAVED"
    assert [item["url"] for item in single["env"]["created"]] == ["blob:task-1"]
    assert single["env"]["revoked"] == ["blob:task-1"]


def test_retry_reuses_the_same_binding(artifact_logic: dict[str, Any]) -> None:
    retry = artifact_logic["retry"]
    assert len(retry["requests"]) == 2
    first, second = retry["requests"]
    assert (first["url"], first["params"]) == (second["url"], second["params"])
    assert retry["entry"] == {"phase": "SAVED", "errorCode": None}
    created_urls = [item["url"] for item in retry["env"]["created"]]
    assert retry["env"]["revoked"] == created_urls and len(created_urls) == 1


def test_identity_reset_discards_late_results(artifact_logic: dict[str, Any]) -> None:
    fence = artifact_logic["resetFence"]
    assert fence["entry"] == {"phase": "IDLE", "errorCode": None}
    assert fence["env"]["created"] == [], "重置后的迟到响应不得创建 URL"


def test_save_stage_failure_still_releases_the_url(artifact_logic: dict[str, Any]) -> None:
    failure = artifact_logic["saveFailure"]
    assert failure["entry"]["phase"] == "FAILED"
    assert failure["key"] == "chat.taskCenter.artifact.error.failed"
    created_urls = [item["url"] for item in failure["env"]["created"]]
    assert failure["env"]["revoked"] == created_urls
    assert len(created_urls) == 1, "点击抛错也要释放 URL"


def test_blank_artifact_id_never_reaches_http(artifact_logic: dict[str, Any]) -> None:
    blank = artifact_logic["blankId"]
    assert blank["requests"] == 0
    assert blank["entry"] == {"phase": "IDLE", "errorCode": None}


def test_each_artifact_keeps_its_own_version(artifact_logic: dict[str, Any]) -> None:
    two = artifact_logic["twoArtifacts"]
    assert [request["url"] for request in two["requests"]] == [
        f"/webchat/task-center/artifacts/{ARTIFACT_1}",
        f"/webchat/task-center/artifacts/{ARTIFACT_2}",
    ]
    assert [request["params"] for request in two["requests"]] == [
        {"version": VERSION, "checksum": CHECKSUM},
        {"version": "v9", "checksum": CHECKSUM_2},
    ]
    assert [entry["phase"] for entry in two["entries"]] == ["SAVED", "SAVED"]
    created_urls = [item["url"] for item in two["env"]["created"]]
    assert two["env"]["revoked"] == created_urls and len(created_urls) == 2


def _tag(markup: str, testid: str) -> str:
    match = re.search(rf"<[a-zA-Z][^>]*data-testid=\"{re.escape(testid)}\"[^>]*>", markup)
    assert match, f"未找到元素：{testid}"
    return match.group(0)


def test_artifact_list_renders_every_immutable_fact(artifact_render: dict[str, Any]) -> None:
    markup = artifact_render["markup"]["saved-zh"]
    assert markup.count('data-testid="chat-task-artifact"') == 2
    assert "weekly.md" in markup and "text/markdown" in markup
    assert "2.0 KB" in markup and "1.0 MB" in markup
    assert VERSION in markup and CHECKSUM in markup and CHECKSUM_2 in markup
    tag = _tag(markup, "chat-task-artifact")
    assert f'data-artifact-version="{VERSION}"' in tag
    assert f'data-artifact-checksum="{CHECKSUM}"' in tag
    assert 'aria-label="下载 weekly.md"' in markup
    assert "已保存" in markup
    assert 'data-testid="chat-task-artifact-error"' not in markup


def test_artifact_download_errors_are_visible_and_not_success(
    artifact_render: dict[str, Any],
) -> None:
    markup = artifact_render["markup"]
    conflict = markup["failed-conflict-zh"]
    assert "产物版本或校验值不一致，请刷新后重试" in conflict
    assert 'role="alert"' in conflict
    assert "已保存" not in conflict, "失败不能显示成功态"
    assert 'data-testid="chat-task-artifact-saved"' not in conflict

    unknown = markup["failed-unknown-zh"]
    assert "下载失败，请重试" in unknown
    assert "（WEIRD_CODE）" in unknown, "未知码有可读兜底"

    english = markup["failed-en"]
    assert "Artifact not found or not accessible" in english


def test_artifact_states_are_real(artifact_render: dict[str, Any]) -> None:
    markup = artifact_render["markup"]
    assert "下载中…" in markup["downloading-zh"]
    assert "本次执行没有产物" in markup["empty-zh"]
    assert 'data-testid="chat-task-artifacts"' not in markup["empty-zh"]
