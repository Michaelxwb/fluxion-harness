"""[B-272/B-101/B-105] 浏览器 SSE 与 snapshot 分片解析器（design frontend §3.1/§3.5）。

真实边界：真实共享流模块 `src/api/stream.ts`（复用 `src/api/client.ts` 的传输头/追踪 ID/CSRF 接缝与
`src/api/authScope.ts` 的 CONSOLE/CHAT 域判定、`modules/webchat/types.ts` 的 typed DTO）由仓库内
esbuild 打成真实 ESM 编译产物，真实 node 执行解析/状态/重连逻辑（DOM 交互与真实 SSE 留浏览器 E2E）。
断言覆盖：

- **解析**（B-101）：跨 chunk UTF-8（逐字节切分）、CRLF、多行 `data:`、心跳/注释帧、重复 seq、
  合法跳号、截断帧丢弃、坏 `id` 显式失败。
- **边界**（B-272）：单帧按 UTF-8 字节受 `sse_event_max_bytes` 约束，超界显式失败且缓冲有界；
  失败后解析器仍可用。
- **分片**（B-105）：同 `event_id/seq/reply_generation` 的 part 全部到齐才原子替换正文并推进游标，
  拼接逐字节等于原文；缺片/重复片/片间插入/断流按安全游标恢复，checkpoint 不跨未齐组；分代切换
  绝不拼出半份/跨代正文。
- **重连**（B-272）：`after_seq`/`Last-Event-ID` 用已应用水位（游标排他），EOF 不当完成，
  `CURSOR_EXPIRED` 显式上抛，CHAT 401 通知认证层，退避/次数来自配置投影。
- **版本绑定**（B-105）：连接创建时冻结 `transport_revision` 与传输参数，配置更新只影响新连接。
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
API_DIR = SRC / "api"
STREAM_TS = API_DIR / "stream.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"
TYPES_TS = SRC / "modules/webchat/types.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

RUN_ID = "33333333-3333-4333-8333-333333333333"
CONVERSATION_ID = "22222222-2222-4222-8222-222222222222"
SESSION_ID = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
"""

ENTRY = """export * from '__STREAM__';
export { setChatContextId, onChatUnauthorized } from '__AUTH_SCOPE__';
"""

HARNESS_JS = r"""
const toasts = [];
const redirects = [];
const chatUnauthorizedEvents = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-272' };
globalThis.window = {
  location: { pathname: '/chat/entry-272', search: '', assign: (url) => { redirects.push(url); } }
};

const mod = await import('./stream.mjs');
const {
  createSseFrameParser,
  createReplyStreamCollector,
  openReplyStream,
  reconnectDelayMs,
  webchatStreamErrorCode,
  isWebchatStreamCursorError,
  isWebchatStreamAuthError,
  isWebchatStreamRetryableError,
  setChatContextId,
  onChatUnauthorized
} = mod;

onChatUnauthorized(() => { chatUnauthorizedEvents.push('chat-unauthorized'); });

const RUN_ID = '__RUN_ID__';
const CONVERSATION_ID = '__CONVERSATION_ID__';
const SESSION_ID = '__SESSION_ID__';
const ENCODER = new TextEncoder();
const bytesOf = (text) => Array.from(ENCODER.encode(text));
const splitBytes = (text, size) => {
  const all = bytesOf(text);
  const chunks = [];
  for (let index = 0; index < all.length; index += size) chunks.push(all.slice(index, index + size));
  return chunks;
};
const eventIdOf = (seq) => 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0');
const envelope = (type, seq, data) => JSON.stringify({
  event_id: eventIdOf(seq),
  conversation_id: CONVERSATION_ID,
  run_id: RUN_ID,
  seq,
  timestamp: '2026-10-18T00:00:00Z',
  type,
  data
});
const frameOf = (id, event, data) => `id: ${id}\nevent: ${event}\ndata: ${envelope(event, id, data)}\n\n`;
const checkpointEnvelope = (seq) => JSON.stringify({
  type: 'checkpoint',
  conversation_id: CONVERSATION_ID,
  run_id: RUN_ID,
  seq,
  timestamp: '2026-10-18T00:00:00Z',
  data: {}
});
const checkpointFrame = (seq) => `id: ${seq}\nevent: checkpoint\ndata: ${checkpointEnvelope(seq)}\n\n`;
const snapshotFrames = (seq, generation, text, count) => {
  const chars = Array.from(text);
  const size = Math.ceil(chars.length / count);
  const frames = [];
  for (let index = 0; index < count; index += 1) {
    frames.push(frameOf(seq, 'message.snapshot', {
      reply_generation: generation,
      text: chars.slice(index * size, (index + 1) * size).join(''),
      part_index: index,
      part_count: count
    }));
  }
  return frames;
};
const applyEvent = (collector, id, event, data) =>
  collector.apply({ cursor: id, event, data: envelope(event, id, data) });
const applyCheckpoint = (collector, seq) =>
  collector.apply({ cursor: seq, event: 'checkpoint', data: checkpointEnvelope(seq) });
const applyCapture = (collector, id, event, data) => {
  try {
    return { update: applyEvent(collector, id, event, data) };
  } catch (error) {
    return { error: error && error.code ? error.code : 'unknown' };
  }
};
const stateOf = (collector) => ({ ...collector.state(), resume: collector.resumeCursor() });
const updateOf = (result) => (result.update ? result.update : { kind: 'threw', error: result.error });

const obs = {};

// ---- 1) 解析：跨 chunk UTF-8 / CRLF / 多行 data / 心跳 ---------------------------------------
const deltaData = { reply_generation: 0, text: '正文🌏' };
const deltaFrame = frameOf(3, 'message.delta', deltaData);

{
  const frames = [];
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: (frame) => frames.push(frame) });
  for (const chunk of splitBytes(deltaFrame, 1)) parser.push(new Uint8Array(chunk));
  parser.end();
  obs.bytewiseChunks = {
    count: frames.length,
    cursor: frames[0] ? frames[0].cursor : null,
    event: frames[0] ? frames[0].event : null,
    text: frames[0] ? JSON.parse(frames[0].data).data.text : null
  };
}

{
  const crlf = `id: 3\r\nevent: message.delta\r\ndata: ${envelope('message.delta', 3, deltaData)}\r\n\r\n`;
  const frames = [];
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: (frame) => frames.push(frame) });
  for (const chunk of splitBytes(crlf, 1)) parser.push(new Uint8Array(chunk));
  parser.end();
  obs.crlf = {
    count: frames.length,
    cursor: frames[0] ? frames[0].cursor : null,
    text: frames[0] ? JSON.parse(frames[0].data).data.text : null
  };
}

{
  const json = envelope('message.delta', 3, deltaData);
  const cut = json.indexOf(',"data"') + 1;
  const text =
    `: heartbeat\n\nid: 3\n: ping\nevent: message.delta\n` +
    `data: ${json.slice(0, cut)}\ndata: ${json.slice(cut)}\n\n`;
  const frames = [];
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: (frame) => frames.push(frame) });
  parser.push(ENCODER.encode(text));
  parser.end();
  obs.multilineData = {
    count: frames.length,
    cursor: frames[0] ? frames[0].cursor : null,
    text: frames[0] ? JSON.parse(frames[0].data).data.text : null
  };
}

{
  const frames = [];
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: (frame) => frames.push(frame) });
  parser.push(ENCODER.encode(': heartbeat\n\n'));
  parser.end();
  obs.heartbeatOnly = { count: frames.length };
}

{
  const frames = [];
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: (frame) => frames.push(frame) });
  parser.push(ENCODER.encode('id: 9\nevent: message.delta\ndata: {"x":1}'));
  parser.end();
  obs.truncatedFrame = { count: frames.length };
}

{
  let code = null;
  const parser = createSseFrameParser({ maxFrameBytes: 262144, onFrame: () => {} });
  try {
    parser.push(ENCODER.encode('id: not-a-seq\ndata: {}\n\n'));
  } catch (error) {
    code = error && error.code ? error.code : 'unknown';
  }
  obs.malformedId = { code };
}

// ---- 2) 单帧字节边界（B-272：缓冲和单帧受有效配置约束） ---------------------------------------
{
  const text = frameOf(4, 'message.delta', { reply_generation: 0, text: 'x'.repeat(40) });
  const exact = bytesOf(text).length;
  const accepted = [];
  const parserA = createSseFrameParser({ maxFrameBytes: exact, onFrame: (frame) => accepted.push(frame) });
  parserA.push(ENCODER.encode(text));
  parserA.end();

  let overCode = null;
  const parserB = createSseFrameParser({ maxFrameBytes: exact - 1, onFrame: () => {} });
  try {
    parserB.push(ENCODER.encode(text));
  } catch (error) {
    overCode = error && error.code ? error.code : 'unknown';
  }

  const recovered = [];
  const parserC = createSseFrameParser({
    maxFrameBytes: exact - 1,
    onFrame: (frame) => recovered.push(frame)
  });
  try {
    parserC.push(ENCODER.encode(text));
  } catch (error) {
    // 预期：超界帧显式失败且缓冲被清空
  }
  parserC.push(ENCODER.encode(frameOf(5, 'message.delta', { reply_generation: 0, text: 'ok' })));
  parserC.end();

  const wideText = frameOf(6, 'message.delta', { reply_generation: 0, text: '🌏'.repeat(30) });
  let byteCode = null;
  const parserD = createSseFrameParser({ maxFrameBytes: wideText.length, onFrame: () => {} });
  try {
    parserD.push(ENCODER.encode(wideText));
  } catch (error) {
    byteCode = error && error.code ? error.code : 'unknown';
  }

  obs.frameBudget = {
    exact,
    accepted: accepted.length,
    overCode,
    recovered: recovered.length,
    recoveredCursor: recovered[0] ? recovered[0].cursor : null,
    wideChars: wideText.length,
    wideBytes: bytesOf(wideText).length,
    byteCode
  };
}

// ---- 3) 重复 seq / 合法跳号（B-101） ---------------------------------------------------------
{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  const results = [
    updateOf(applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '甲' })),
    updateOf(applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '甲' })),
    updateOf(applyCapture(collector, 5, 'message.delta', { reply_generation: 0, text: '乙' }))
  ];
  obs.duplicateAndGaps = {
    kinds: results.map((update) => update.kind),
    reasons: results.map((update) => update.reason || null),
    text: collector.state().text,
    resume: collector.resumeCursor()
  };
}

// ---- 4) snapshot 分片：全部到齐才原子替换（B-105） -------------------------------------------
{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '旧代' });
  const parts = ['片零🌏', '片一🌏', '片二🌏'];
  const snapshotText = parts.join('');
  const results = [
    updateOf(applyCapture(collector, 9, 'message.snapshot', {
      reply_generation: 1, text: parts[1], part_index: 1, part_count: 3
    })),
    updateOf(applyCapture(collector, 9, 'message.snapshot', {
      reply_generation: 1, text: parts[0], part_index: 0, part_count: 3
    }))
  ];
  const midState = stateOf(collector);
  results.push(updateOf(applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: parts[2], part_index: 2, part_count: 3
  })));
  const finalState = stateOf(collector);
  const tail = updateOf(applyCapture(collector, 11, 'message.delta', { reply_generation: 1, text: '续写' }));
  obs.snapshotAtomic = {
    kinds: results.map((update) => update.kind),
    received: results.map((update) => (update.received === undefined ? null : update.received)),
    midText: midState.text,
    midResume: midState.resume,
    midPending: midState.pendingParts,
    text: finalState.text,
    expected: snapshotText,
    byteExact: bytesOf(finalState.text).join() === bytesOf(snapshotText).join(),
    resume: finalState.resume,
    generation: finalState.replyGeneration,
    tailKind: tail.kind,
    tailText: tail.text,
    tailDelta: tail.delta
  };
}

// ---- 5) 缺片 / 重复片 / 片间插入 / checkpoint / 断流（B-105） ---------------------------------
{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '基' });
  applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: '片零', part_index: 0, part_count: 3
  });
  const duplicate = updateOf(applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: '片零', part_index: 0, part_count: 3
  }));
  const interleaved = applyCapture(collector, 5, 'message.delta', { reply_generation: 0, text: '插入' });
  const afterInterleave = stateOf(collector);
  obs.snapshotGaps = {
    duplicateKind: duplicate.kind,
    duplicateReason: duplicate.reason || null,
    interleavedError: interleaved.error || null,
    text: afterInterleave.text,
    resume: afterInterleave.resume,
    pending: afterInterleave.pendingParts
  };
}

{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '基' });
  applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: '片零', part_index: 0, part_count: 2
  });
  const checkpoint6 = updateOf({ update: applyCheckpoint(collector, 6) });
  const resume6 = collector.resumeCursor();
  const checkpoint20 = updateOf({ update: applyCheckpoint(collector, 20) });
  const resume20 = collector.resumeCursor();
  const pendingState = stateOf(collector);
  collector.disconnect();
  const afterDisconnect = stateOf(collector);
  applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: '片零', part_index: 0, part_count: 2
  });
  applyCapture(collector, 9, 'message.snapshot', {
    reply_generation: 1, text: '片一', part_index: 1, part_count: 2
  });
  const replayed = stateOf(collector);
  obs.checkpointAndDisconnect = {
    checkpointKind: checkpoint6.kind,
    resume6,
    checkpoint20Cursor: checkpoint20.cursor,
    resume20,
    pendingText: pendingState.text,
    pendingParts: pendingState.pendingParts,
    afterDisconnectText: afterDisconnect.text,
    afterDisconnectResume: afterDisconnect.resume,
    afterDisconnectPending: afterDisconnect.pendingParts,
    replayedText: replayed.text,
    replayedResume: replayed.resume,
    replayedPending: replayed.pendingParts
  };
}

// ---- 6) 分代：snapshot 整组替换，跨代/迟到绝不拼接（B-105/B-101） -----------------------------
{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  applyCapture(collector, 1, 'message.delta', { reply_generation: 0, text: '旧代' });
  const jump = applyCapture(collector, 2, 'message.delta', { reply_generation: 1, text: '新代' });
  const afterJump = stateOf(collector);
  applyCapture(collector, 3, 'message.snapshot', {
    reply_generation: 1, text: '替换', part_index: 0, part_count: 1
  });
  const afterSnapshot = stateOf(collector);
  const stale = updateOf(applyCapture(collector, 4, 'message.delta', { reply_generation: 0, text: '迟到' }));
  const afterStale = stateOf(collector);
  const fresh = updateOf(applyCapture(collector, 5, 'message.delta', { reply_generation: 1, text: '续' }));
  obs.generation = {
    jumpError: jump.error || null,
    textAfterJump: afterJump.text,
    generationAfterJump: afterJump.replyGeneration,
    snapshotText: afterSnapshot.text,
    snapshotGeneration: afterSnapshot.replyGeneration,
    staleKind: stale.kind,
    staleReason: stale.reason || null,
    textAfterStale: afterStale.text,
    resumeAfterStale: afterStale.resume,
    freshKind: fresh.kind,
    freshText: fresh.text
  };
}

// ---- 7) 传输：重连 / 游标 / 认证 / 版本绑定 ---------------------------------------------------
const requests = [];
let scripts = [];
globalThis.fetch = async (url, init) => {
  const headers = { ...(init && init.headers ? init.headers : {}) };
  requests.push({
    url: String(url),
    method: (init && init.method) || 'GET',
    headers,
    signal: init ? init.signal : null
  });
  const spec = scripts.shift() || { kind: 'eof' };
  if (spec.kind === 'http-error') {
    return new Response(JSON.stringify(spec.body), {
      status: spec.status,
      headers: { 'content-type': 'application/json' }
    });
  }
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of spec.chunks || []) controller.enqueue(new Uint8Array(chunk));
      if (spec.end === false) {
        const signal = init ? init.signal : null;
        if (signal) {
          signal.addEventListener('abort', () => {
            try {
              controller.error(new DOMException('aborted', 'AbortError'));
            } catch (error) {
              // 已关闭的控制器：无需再传播
            }
          });
        }
      } else {
        controller.close();
      }
    }
  });
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
};

const streamSpec = (frames, options = {}) => ({
  kind: 'stream',
  chunks: frames.map((text) => bytesOf(text)),
  ...options
});
const configOf = (overrides = {}) => ({
  settings_revision: 12,
  transport_revision: overrides.transport_revision || 'rev-272',
  webchat: {
    history_page_size: 50,
    text_max_bytes: 8192,
    upload_max_bytes: 10485760,
    upload_max_per_message: 5,
    notification_poll_sec: 30
  },
  transport: {
    sse_event_max_bytes: overrides.sse_event_max_bytes || 262144,
    sse_heartbeat_sec: overrides.sse_heartbeat_sec || 15,
    reconnect_base_ms: overrides.reconnect_base_ms === undefined ? 1 : overrides.reconnect_base_ms,
    reconnect_cap_ms: overrides.reconnect_cap_ms === undefined ? 4 : overrides.reconnect_cap_ms,
    reconnect_max_attempts:
      overrides.reconnect_max_attempts === undefined ? 3 : overrides.reconnect_max_attempts
  }
});
const stopOf = async (stream) => {
  const stop = await stream.done;
  const error = stop.error || null;
  return {
    kind: stop.kind,
    status: stop.status || null,
    code: stop.code || (error ? error.code : null),
    retryable: stop.retryable === undefined ? null : stop.retryable,
    httpStatus: error ? error.status : null,
    cursor: error ? error.cursor : null,
    errorCode: error ? webchatStreamErrorCode(error) : null,
    cursorError: error ? isWebchatStreamCursorError(error) : false,
    authError: error ? isWebchatStreamAuthError(error) : false,
    retryableError: error ? isWebchatStreamRetryableError(error) : false
  };
};
const waitForRequests = async (count) => {
  for (let index = 0; index < 400; index += 1) {
    if (requests.length >= count) return true;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  return false;
};

// 7a) 断流重连：已应用水位续传（游标排他），终态停止重连
{
  const updates = [];
  setChatContextId(null);
  scripts = [
    streamSpec([
      frameOf(1, 'message.delta', { reply_generation: 0, text: 'A' }),
      ...snapshotFrames(9, 1, 'S🌏S', 3),
      frameOf(11, 'message.delta', { reply_generation: 1, text: 'B' }),
      checkpointFrame(14)
    ]),
    streamSpec([
      frameOf(15, 'message.delta', { reply_generation: 1, text: 'C' }),
      frameOf(16, 'run.terminal', { status: 'COMPLETED' })
    ])
  ];
  const start = requests.length;
  const stream = openReplyStream({
    runId: RUN_ID,
    config: configOf(),
    onUpdate: (update) => updates.push(update.kind)
  });
  const stop = await stopOf(stream);
  obs.reconnect = {
    revision: stream.transportRevision,
    stop,
    updates,
    text: stream.state().text,
    cursor: stream.cursor(),
    requests: requests.slice(start).map((entry) => ({
      url: entry.url,
      lastEventId: entry.headers['Last-Event-ID'] || null,
      locale: entry.headers['X-Locale'] || null,
      requestId: entry.headers['X-Request-Id'] || null,
      csrf: entry.headers['X-CSRF-Token'] || null,
      chatContext: entry.headers['X-Chat-Context'] || null
    }))
  };
}

// 7b) CURSOR_EXPIRED 显式上抛：不自动补拉、窗口原样带出
{
  scripts = [{
    kind: 'http-error',
    status: 410,
    body: { code: 'CURSOR_EXPIRED', msg: '游标过期', data: { after_seq: 2, earliest_seq: 5 } }
  }];
  const start = requests.length;
  const stream = openReplyStream({ runId: RUN_ID, config: configOf(), afterSeq: 2 });
  const stop = await stopOf(stream);
  obs.cursorExpired = {
    stop,
    requests: requests.length - start,
    url: requests[start].url,
    lastEventId: requests[start].headers['Last-Event-ID'] || null
  };
}

// 7c) CHAT 401：通知认证层，不跳 Console 登录、不叠加 Toast
{
  scripts = [{
    kind: 'http-error',
    status: 401,
    body: { code: 'CHAT_SESSION_EXPIRED', msg: '会话已过期' }
  }];
  const before = chatUnauthorizedEvents.length;
  const stream = openReplyStream({ runId: RUN_ID, config: configOf() });
  const stop = await stopOf(stream);
  obs.authRequired = {
    stop,
    unauthorizedEvents: chatUnauthorizedEvents.length - before,
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// 7d) 版本绑定：活跃连接冻结旧传输参数，新连接读取新版本
{
  const config = configOf({
    transport_revision: 'rev-old',
    sse_event_max_bytes: 200,
    reconnect_max_attempts: 0
  });
  const oversized = frameOf(1, 'message.delta', { reply_generation: 0, text: 'x'.repeat(400) });
  scripts = [streamSpec([oversized])];
  const start = requests.length;
  const stream = openReplyStream({ runId: RUN_ID, config });
  config.transport_revision = 'rev-new';
  config.transport.sse_event_max_bytes = 4096;
  config.transport.reconnect_max_attempts = 2;
  const stop = await stopOf(stream);
  const oldRequests = requests.length - start;

  scripts = [streamSpec([oversized, frameOf(2, 'run.terminal', { status: 'COMPLETED' })])];
  const stream2 = openReplyStream({ runId: RUN_ID, config });
  const stop2 = await stopOf(stream2);
  obs.revisionBinding = {
    old: { revision: stream.transportRevision, stop, requests: oldRequests },
    fresh: {
      revision: stream2.transportRevision,
      stop: stop2,
      text: stream2.state().text,
      cursor: stream2.cursor()
    }
  };
}

// 7e) close() 中止活跃连接
{
  scripts = [streamSpec([frameOf(1, 'message.delta', { reply_generation: 0, text: '开' })], { end: false })];
  const start = requests.length;
  const stream = openReplyStream({ runId: RUN_ID, config: configOf() });
  const connected = await waitForRequests(start + 1);
  stream.close();
  const stop = await stopOf(stream);
  obs.closeMidStream = {
    connected,
    stop,
    aborted: requests[start].signal ? requests[start].signal.aborted : null
  };
}

// 7f) CHAT 上下文接缝：bootstrap 后新连接携带验证上下文
{
  setChatContextId(SESSION_ID);
  scripts = [streamSpec([frameOf(1, 'run.terminal', { status: 'COMPLETED' })])];
  const start = requests.length;
  const stream = openReplyStream({ runId: RUN_ID, config: configOf() });
  await stopOf(stream);
  obs.contextSeam = {
    chatContext: requests[start].headers['X-Chat-Context'] || null,
    csrf: requests[start].headers['X-CSRF-Token'] || null
  };
}

// ---- 8) 退避：配置投影驱动、有界、含抖动 ------------------------------------------------------
{
  const transport = {
    sse_event_max_bytes: 262144,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 1000,
    reconnect_cap_ms: 30000,
    reconnect_max_attempts: 6
  };
  obs.delays = {
    first: [reconnectDelayMs(1, transport, () => 0), reconnectDelayMs(1, transport, () => 1)],
    second: [reconnectDelayMs(2, transport, () => 0), reconnectDelayMs(2, transport, () => 1)],
    capped: [reconnectDelayMs(10, transport, () => 0), reconnectDelayMs(10, transport, () => 1)]
  };
}

console.log(JSON.stringify(obs));
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


def _run_stream_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包 `stream.ts`（含共享传输/authScope）并执行解析与重连。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = ENTRY.replace("__STREAM__", str(STREAM_TS)).replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
    (tmp_path / "entry.ts").write_text(entry, encoding="utf-8")
    harness = (
        HARNESS_JS.replace("__RUN_ID__", RUN_ID)
        .replace("__CONVERSATION_ID__", CONVERSATION_ID)
        .replace("__SESSION_ID__", SESSION_ID)
    )
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'stream.mjs'}",
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
def stream(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_stream_harness(tmp_path_factory.mktemp("browser-sse-parser"))


def test_b101_parser_covers_chunk_splits_crlf_multiline_and_heartbeats(stream: dict[str, Any]) -> None:
    """跨 chunk UTF-8（逐字节）、CRLF、多行 data、心跳/注释、截断帧与坏 id 的显式行为。"""
    bytewise = stream["bytewiseChunks"]
    assert bytewise == {"count": 1, "cursor": 3, "event": "message.delta", "text": "正文🌏"}, (
        "逐字节切分（含跨 UTF-8 边界）后必须解析出唯一完整帧且正文逐字一致"
    )
    crlf = stream["crlf"]
    assert crlf["count"] == 1 and crlf["cursor"] == 3 and crlf["text"] == "正文🌏", (
        f"CRLF 行结束符必须等价于 LF：{crlf}"
    )
    multiline = stream["multilineData"]
    assert multiline == {"count": 1, "cursor": 3, "text": "正文🌏"}, (
        f"多行 data 必须按换行拼接后解析，注释/心跳不得产生帧：{multiline}"
    )
    assert stream["heartbeatOnly"] == {"count": 0}, "心跳注释帧不得产生事件、不得推进游标"
    assert stream["truncatedFrame"] == {"count": 0}, "EOF 时未收齐的帧必须丢弃（游标未推进，可重投）"
    assert stream["malformedId"] == {"code": "MALFORMED_FRAME"}, "坏 id 必须显式失败，不猜测游标"


def test_b272_single_frame_budget_is_enforced_in_utf8_bytes(stream: dict[str, Any]) -> None:
    """单帧按 UTF-8 字节受 `sse_event_max_bytes` 约束：正好等于上限通过、超一字节显式失败。"""
    budget = stream["frameBudget"]
    assert budget["accepted"] == 1, "帧字节数恰好等于上限必须通过"
    assert budget["overCode"] == "FRAME_TOO_LARGE", f"超界帧必须显式失败：{budget}"
    assert budget["recovered"] == 1 and budget["recoveredCursor"] == 5, (
        "超界失败后缓冲必须被清空，解析器仍可继续（有界，不无限累积）"
    )
    assert budget["wideBytes"] > budget["wideChars"], "构造必须覆盖字节 > 字符的多字节帧"
    assert budget["byteCode"] == "FRAME_TOO_LARGE", (
        "上限按 UTF-8 字节而非 JS 字符计数（多字节正文不得绕过预算）"
    )


def test_b101_duplicate_seq_and_legal_gaps_are_explicit(stream: dict[str, Any]) -> None:
    """重复 seq 按 Run/seq 去重；过滤后的合法跳号不触发补拉也不报错。"""
    observed = stream["duplicateAndGaps"]
    assert observed["kinds"] == ["delta", "ignored", "delta"], f"重复 seq 必须显式 ignored：{observed}"
    assert observed["reasons"] == [None, "DUPLICATE_SEQ", None]
    assert observed["text"] == "甲乙", "重复 delta 不得追加两次"
    assert observed["resume"] == 5, "合法跳号（1 → 5）后游标推进到真实应用水位"


def test_b105_snapshot_parts_apply_atomically_only_when_complete(stream: dict[str, Any]) -> None:
    """同 canonical seq 的 part 全部到齐才原子替换正文并推进游标；拼接逐字节等于原文。"""
    observed = stream["snapshotAtomic"]
    assert observed["kinds"] == ["snapshot-part", "snapshot-part", "snapshot"], (
        f"未齐组只能报告分片进度，不得替换正文：{observed}"
    )
    assert observed["received"] == [1, 2, None], "分片进度必须显式（收到片数）"
    assert observed["midText"] == "旧代" and observed["midResume"] == 1, (
        "片未齐时不得替换正文、不得推进已应用游标"
    )
    assert observed["midPending"] == 1, "未齐组必须保持待收状态"
    assert observed["text"] == observed["expected"], "整组到齐后必须原子替换为完整正文"
    assert observed["byteExact"] is True, "拼接结果必须逐字节等于原文（含多字节字符）"
    assert observed["resume"] == 9 and observed["generation"] == 1, "应用后游标推进到该 canonical seq"
    assert observed["tailKind"] == "delta" and observed["tailDelta"] == "续写", (
        "整组之后的新代 delta 继续追加"
    )
    assert observed["tailText"] == observed["expected"] + "续写"


def test_b105_missing_duplicate_parts_and_checkpoints_keep_the_safe_cursor(stream: dict[str, Any]) -> None:
    """重复片去重、片间插入/断流丢弃未齐组、checkpoint 不跨未齐组：游标始终留在安全水位。"""
    gaps = stream["snapshotGaps"]
    assert gaps["duplicateKind"] == "ignored" and gaps["duplicateReason"] == "DUPLICATE_PART", (
        f"重复片必须去重，不得重复累计：{gaps}"
    )
    assert gaps["interleavedError"] == "SNAPSHOT_INCOMPLETE", (
        "片间插入其他事件说明缺片，必须显式按安全游标恢复，不得继续拼正文"
    )
    assert gaps["text"] == "基" and gaps["resume"] == 1 and gaps["pending"] == 0, (
        "恢复后正文与游标必须停在安全水位，不拼出半份正文"
    )

    checkpoint = stream["checkpointAndDisconnect"]
    assert checkpoint["checkpointKind"] == "checkpoint"
    assert checkpoint["resume6"] == 6, "checkpoint 推进已扫描安全水位（不追加正文）"
    assert checkpoint["resume20"] == 8, "checkpoint 不得跨越未齐组（seq 9 的组必须可重投）"
    assert checkpoint["pendingText"] == "基" and checkpoint["pendingParts"] == 1
    assert checkpoint["afterDisconnectPending"] == 0, "断流必须丢弃未齐组"
    assert checkpoint["afterDisconnectResume"] == 8, (
        "断流后游标保持安全水位：checkpoint 20 被钳在未齐组（seq 9）之前 = 8，组仍可重投"
    )
    assert checkpoint["replayedText"] == "片零片一", "重连重投整组后必须能完整应用"
    assert checkpoint["replayedResume"] == 9 and checkpoint["replayedPending"] == 0


def test_b105_generation_switch_never_mixes_reply_text(stream: dict[str, Any]) -> None:
    """snapshot 整组替换才开启新代；没有 snapshot 的分代跳跃与迟到旧代输出都不进正文。"""
    observed = stream["generation"]
    assert observed["jumpError"] == "MALFORMED_FRAME", "没有整组 snapshot 的分代跳跃必须显式失败"
    assert observed["textAfterJump"] == "旧代" and observed["generationAfterJump"] == 0, (
        "分代跳跃不得把另一代的正文拼进当前代"
    )
    assert observed["snapshotText"] == "替换" and observed["snapshotGeneration"] == 1
    assert observed["staleKind"] == "ignored" and observed["staleReason"] == "STALE_GENERATION", (
        "旧代迟到 delta 必须被 fence（显式 ignored）"
    )
    assert observed["textAfterStale"] == "替换" and observed["resumeAfterStale"] == 3, (
        "迟到旧代输出不得改正文、不得推进游标"
    )
    assert observed["freshKind"] == "delta" and observed["freshText"] == "替换续"


def test_b272_transport_resumes_by_applied_cursor_and_stops_on_terminal(stream: dict[str, Any]) -> None:
    """断流只重连 GET：after_seq/Last-Event-ID 用已应用水位；终态停止重连，EOF 不当完成。"""
    observed = stream["reconnect"]
    assert observed["revision"] == "rev-272"
    assert observed["updates"] == [
        "delta",
        "snapshot-part",
        "snapshot-part",
        "snapshot",
        "delta",
        "checkpoint",
        "delta",
        "terminal",
    ], f"应用顺序必须与公开事件顺序一致：{observed['updates']}"
    assert observed["stop"]["kind"] == "terminal" and observed["stop"]["status"] == "COMPLETED", (
        "收到持久终态才停止，且停止原因显式"
    )
    assert observed["text"] == "S🌏SBC" and observed["cursor"] == 16
    requests = observed["requests"]
    assert len(requests) == 2, "断流后必须重连一次（EOF 不当完成）"
    first, second = requests
    assert first["url"] == f"/api/v1/webchat/runs/{RUN_ID}/events", "首连无本地游标时不带查询参数"
    assert first["lastEventId"] is None
    assert second["url"] == f"/api/v1/webchat/runs/{RUN_ID}/events?after_seq=14", (
        "重连必须用已应用/已扫描安全水位（含 checkpoint 14），游标排他"
    )
    assert second["lastEventId"] == "14", "重连必须携带 Last-Event-ID 续传水位"
    for entry in requests:
        assert entry["locale"] == "zh-CN" and entry["requestId"], f"共享传输头必须齐备：{entry}"
        assert entry["csrf"] == "chat-csrf-272", "SSE 的 CSRF 只取自 CHAT 域 Cookie"
        assert entry["chatContext"] is None, "bootstrap 之前不得伪造 X-Chat-Context"


def test_b272_cursor_expiry_is_explicit_and_not_auto_repulled(stream: dict[str, Any]) -> None:
    """CURSOR_EXPIRED 显式上抛（含服务端窗口）：不自动补拉、不静默夹取。"""
    observed = stream["cursorExpired"]
    stop = observed["stop"]
    assert observed["requests"] == 1, "游标过期不得自动重连（恢复走详情/安全快照）"
    assert stop["kind"] == "cursor-recovery"
    assert stop["errorCode"] == "CURSOR_EXPIRED" and stop["httpStatus"] == 410
    assert stop["cursorError"] is True and stop["retryableError"] is False
    assert stop["cursor"] == {"after_seq": 2, "earliest_seq": 5}, "服务端窗口必须原样带出"
    assert observed["url"].endswith("/events?after_seq=2"), "恢复游标由调用方持有并显式续传"
    assert observed["lastEventId"] == "2"


def test_b272_chat_401_returns_to_token_access(stream: dict[str, Any]) -> None:
    """CHAT 401：通知认证层、不跳 Console 登录、不叠加全局 Toast。"""
    observed = stream["authRequired"]
    assert observed["stop"]["kind"] == "auth-required"
    assert observed["stop"]["authError"] is True
    assert observed["unauthorizedEvents"] >= 1, "CHAT 401 必须通知认证层关闭流回到 TokenAccess"
    assert observed["redirects"] == [], "SSE 401 不得跳 Console 登录页"
    assert observed["toasts"] == [], "SSE 401 的提示由认证层负责，不得叠加全局 Toast"


def test_b105_transport_revision_binds_active_connections_and_new_ones_rebind(
    stream: dict[str, Any],
) -> None:
    """连接创建时冻结 `transport_revision` 与传输参数：活跃连接不因新配置中途换规则。"""
    observed = stream["revisionBinding"]
    old = observed["old"]
    assert old["revision"] == "rev-old", "活跃连接必须报告创建时绑定的传输版本"
    assert old["stop"]["kind"] == "failed" and old["stop"]["errorCode"] == "FRAME_TOO_LARGE", (
        "调用方随后更新配置不得放宽活跃连接的单帧预算"
    )
    assert old["requests"] == 1, "旧连接的重连次数预算也必须在创建时冻结（attempts=0）"
    fresh = observed["fresh"]
    assert fresh["revision"] == "rev-new", "新连接必须重新读取有效版本"
    assert fresh["stop"]["kind"] == "terminal", "新版本预算下同一帧必须可正常解析"
    assert fresh["text"] == "x" * 400 and fresh["cursor"] == 2


def test_b272_close_aborts_the_active_connection(stream: dict[str, Any]) -> None:
    """close() 必须中止活跃 GET（切换会话/卸载时释放浏览器连接）。"""
    observed = stream["closeMidStream"]
    assert observed["connected"] is True
    assert observed["stop"]["kind"] == "closed"
    assert observed["aborted"] is True, "close() 必须中止 fetch（AbortSignal 已触发）"


def test_b272_context_seam_and_backoff_come_from_the_shared_projection(stream: dict[str, Any]) -> None:
    """X-Chat-Context 只来自验证后的 bootstrap；退避/次数消费配置投影且有界。"""
    seam = stream["contextSeam"]
    assert seam["chatContext"] == SESSION_ID, "SSE 必须经共享接缝携带验证后的 CHAT 上下文"
    assert seam["csrf"] == "chat-csrf-272"

    delays = stream["delays"]
    assert delays["first"] == [500, 1000], "首次退避 = 配置 base 的一半到整值（含抖动）"
    assert delays["second"] == [1000, 2000], "退避按 2 的幂增长"
    assert delays["capped"] == [15000, 30000], "退避不得超过配置 cap"


def test_stream_module_keeps_the_shared_transport_contract() -> None:
    """[RULE-front-001] 流层只经共享传输接缝：不裸用 axios、不自行拼传输头、不落持久存储。"""
    source = _read(STREAM_TS)
    compact = _compact(source)

    assert "from'./authScope'" in compact, "认证域/CHAT 上下文必须复用 authScope"
    assert "from'./client'" in compact, "追踪 ID/CSRF 必须复用共享客户端接缝"
    assert "from'../modules/webchat/types'" in compact, "公开事件载荷必须复用模块 typed DTO"
    assert "csrfTokenForScope" in compact, "SSE 的 CSRF 必须与共享客户端同源"
    assert "newRequestId" in compact and "currentLocale" in compact
    assert "chatContextForScope" in compact, "X-Chat-Context 只按认证域取值"
    assert "notifyChatUnauthorized" in compact, "CHAT 401 必须通知认证层"

    for forbidden in ("axios", "localStorage", "sessionStorage", "document.cookie", "console."):
        assert forbidden not in _strip_comments(source), f"流层不得裸用/落盘/打日志：{forbidden}"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"流层不得出现松散类型：{loose}"

    for symbol in (
        "export function createSseFrameParser(",
        "export function createReplyStreamCollector(",
        "export function openReplyStream(",
        "export function reconnectDelayMs(",
        "export function isWebchatStreamCursorError(",
        "export function isWebchatStreamAuthError(",
        "export function isWebchatStreamRetryableError(",
        "export class WebchatStreamError",
    ):
        assert symbol in source, f"共享流层缺少导出：{symbol}"

    assert "X-Chat-Context" not in source and "X-CSRF-Token" not in source, (
        "传输头名只允许由 client.ts/authScope.ts 定义，流层只引用常量"
    )
    assert "CHAT_CONTEXT_HEADER" in compact and "CSRF_HEADER" in compact

    client = _read(CLIENT_TS)
    assert re.search(r"export function csrfTokenForScope\(", client), (
        "共享客户端必须导出 csrfTokenForScope 供流层复用（唯一凭据取源）"
    )
    types = _read(TYPES_TS)
    assert "sse_event_max_bytes: number;" in types and "reconnect_max_attempts: number;" in types, (
        "传输预算字段必须来自 TASK-057/073 的 typed 投影"
    )
