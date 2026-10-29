"""每用户通知 SSE 的 fetch 读取层：真实 esbuild + node + 假 fetch。

为什么单独测：`EventSource` 不能带 `X-Chat-Context`，通知流必须走 fetch 流；这里钉住
请求头（CHAT 上下文/CSRF/追踪）、`after_seq` 游标、完整 DTO 解析、坏帧与缺上下文显式失败。
真实浏览器链路另由 `webchat` E2E 的 S-105 覆盖。
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
API_DIR = SRC / "api"
NOTIFICATION_STREAM_TS = SRC / "modules/webchat/services/notificationStream.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CONTEXT = "3c3c3c3c-3c3c-4c3c-8c3c-3c3c3c3c3c3c"

SEMI_STUB = """export const Toast = { error: () => {} };
"""

ENTRY = """export * from '__STREAM__';
export { setChatContextId } from '__AUTH_SCOPE__';
"""

HARNESS_JS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=csrf-fetch-1' };
globalThis.window = { location: { pathname: '/chat/entry-notification-stream', search: '' } };

const mod = await import('./notification.mjs');
const { createNotificationStreamPort, notificationFrame, setChatContextId } = mod;

const CONTEXT = '__CONTEXT__';
const encoder = new TextEncoder();
const requests = [];
let responses = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const waitUntil = async (predicate, budgetMs = 1000) => {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(5);
  }
  return predicate();
};

const responseOf = (chunks, { ok = true, status = 200 } = {}) => ({
  ok,
  status,
  body: new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    }
  })
});

globalThis.fetch = async (url, init) => {
  requests.push({ url: String(url), init });
  return responses.shift() ?? responseOf([]);
};

const frameOf = (seq, unread) =>
  `id: ${seq}\nevent: notification\ndata: ${JSON.stringify({
    notification: { id: `n-${seq}`, seq },
    unread_count: unread
  })}\n\n`;

const port = createNotificationStreamPort({
  newConnectionConfig: () => ({ transport: { sse_event_max_bytes: 65536 } })
});
const collect = (options = {}) => {
  const seen = { opened: 0, notifications: [], errors: [] };
  const handle = port.open(
    options.afterSeq ?? 3,
    {
      onOpen: () => { seen.opened += 1; },
      onNotification: (notification, unread) => {
        seen.notifications.push({ seq: notification.seq, unread });
      },
      onError: (error) => { seen.errors.push(error instanceof Error ? error.message : String(error)); }
    },
    { snapshotReady: options.snapshotReady ?? true }
  );
  return { seen, handle };
};

setChatContextId(CONTEXT);
responses = [responseOf([frameOf(5, 2), ': heartbeat\n\n', frameOf(6, 3)])];
const happy = collect();
await waitUntil(() => happy.seen.notifications.length === 2 || happy.seen.errors.length > 0);
happy.handle.close();
await sleep(10);
const happyRequest = requests[0];
const happyResult = {
  opened: happy.seen.opened,
  notifications: happy.seen.notifications,
  url: happyRequest.url,
  credentials: happyRequest.init.credentials,
  context: happyRequest.init.headers['X-Chat-Context'],
  csrf: happyRequest.init.headers['X-CSRF-Token'],
  hasRequestId: typeof happyRequest.init.headers['X-Request-Id'] === 'string'
};

setChatContextId(null);
const missingBefore = requests.length;
const missing = collect({ afterSeq: 0 });
await sleep(10);
const missingResult = {
  requests: requests.length - missingBefore,
  errors: missing.seen.errors.length
};

setChatContextId(CONTEXT);
responses = [responseOf(['event: notification\ndata: {not-json}\n\n'])];
const malformed = collect({ afterSeq: 0 });
await waitUntil(() => malformed.seen.errors.length > 0);
malformed.handle.close();
const malformedResult = {
  notifications: malformed.seen.notifications.length,
  errors: malformed.seen.errors.length,
  parsed: (() => {
    try {
      notificationFrame('{"notification":{"id":"n","seq":1},"unread_count":1}');
      return true;
    } catch {
      return false;
    }
  })()
};

const beforeNoSnapshot = requests.length;
responses = [responseOf([])];
const noSnapshot = collect({ afterSeq: 0, snapshotReady: false });
await sleep(10);
noSnapshot.handle.close();
const noSnapshotUrl = requests[beforeNoSnapshot]?.url;

console.log(JSON.stringify({
  happy: happyResult,
  missing: missingResult,
  malformed: malformedResult,
  noSnapshotUrl
}));
"""


def _toolchain_problem() -> str | None:
    if not ESBUILD.is_file():
        return "esbuild 未安装"
    if NODE is None:
        return "node 未安装"
    return None


@pytest.fixture(scope="module")
def notification_stream(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    tmp_path = tmp_path_factory.mktemp("notification-stream")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = ENTRY.replace("__STREAM__", str(NOTIFICATION_STREAM_TS)).replace(
        "__AUTH_SCOPE__", str(AUTH_SCOPE_TS)
    )
    (tmp_path / "entry.ts").write_text(entry, encoding="utf-8")
    (tmp_path / "harness.mjs").write_text(
        HARNESS_JS.replace("__CONTEXT__", CONTEXT), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'notification.mjs'}",
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


def test_notification_stream_uses_fetch_headers_and_typed_frames(
    notification_stream: dict[str, Any],
) -> None:
    happy = notification_stream["happy"]
    assert happy["opened"] == 1
    assert happy["notifications"] == [
        {"seq": 5, "unread": 2},
        {"seq": 6, "unread": 3},
    ]
    assert happy["url"] == "/api/v1/webchat/notifications/stream?after_seq=3&snapshot=1"
    assert happy["credentials"] == "same-origin"
    assert happy["context"] == CONTEXT, "EventSource 无法带 CHAT 上下文；fetch 流必须带上"
    assert happy["csrf"] == "csrf-fetch-1"
    assert happy["hasRequestId"] is True
    assert (
        notification_stream["noSnapshotUrl"]
        == "/api/v1/webchat/notifications/stream?after_seq=0&snapshot=0"
    ), "未追平的客户端不得声称已有完整快照"


def test_notification_stream_fails_explicitly_without_context(
    notification_stream: dict[str, Any],
) -> None:
    missing = notification_stream["missing"]
    assert missing == {"requests": 0, "errors": 1}, "缺上下文必须零请求且显式失败"


def test_notification_stream_rejects_malformed_frames(
    notification_stream: dict[str, Any],
) -> None:
    malformed = notification_stream["malformed"]
    assert malformed["notifications"] == 0
    assert malformed["errors"] >= 1
    assert malformed["parsed"] is True
