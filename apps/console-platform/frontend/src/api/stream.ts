/**
 * 共享浏览器流层：公开 SSE 解析、snapshot 分片收集与游标重连（design frontend §3.1/§3.5；TASK-072）。
 *
 * 只消费 TASK-006 的公开事件契约与 TASK-061 的公开边分片/游标语义：
 *
 * - **解析**：`id:seq` / `event:type` / 多行 `data:` 的增量解析（跨 chunk UTF-8、CRLF 容错）；
 *   注释帧（心跳）不产生事件、不推进游标；单帧按 **UTF-8 字节** 受有效配置
 *   `transport.sse_event_max_bytes` 约束，超界显式失败且缓冲立即清空（不截断、不静默丢弃）。
 * - **应用**：`message.delta` 按 `reply_generation` 分代追加；`message.snapshot` 按
 *   `event_id/seq/reply_generation` 分组收集 `part_index/part_count`，同组全部到齐才**原子替换**
 *   正文并推进已应用水位；缺片/重复片/片间插入/断流丢弃未齐组，checkpoint 不跨未齐组，
 *   绝不拼出半份或跨代正文。
 * - **重连**：只重连 GET（不重发 POST）；`after_seq`/`Last-Event-ID` 都用已应用/已扫描安全水位
 *   （游标排他），EOF 不当完成；`CURSOR_EXPIRED`/`CURSOR_AHEAD`/`CURSOR_RUN_MISMATCH` 显式上抛
 *   由调用方恢复安全快照，不自动补拉；CHAT 401 通知认证层回到 TokenAccess。
 *   每次连接尝试经 `onAttempt` 显式上报（序号/连续重连次数/本次续传游标），调用方据此展示连接状态，
 *   不自行重算退避。
 * - **版本绑定**：连接创建时冻结 `transport_revision` 与传输参数，活跃连接不因新配置中途换规则；
 *   新连接重新读取有效版本（TASK-057 投影，TASK-076/082 消费）。
 *
 * 传输头（语言/追踪/CSRF/CHAT 上下文）只经 `src/api/` 的共享接缝拼装，本模块不读持久存储、
 * 不打日志；DOM 交互留真实浏览器 E2E，纯解析/状态由真实编译产物在 Node 下驱动。
 */

import { currentLocale } from '../i18n';
import type {
  RunStatus,
  WebchatAttachment,
  WebchatClientConfig,
  WebchatClientTransport,
  WebchatConfirmation,
  WebchatExecutionStatus
} from '../modules/webchat/types';

import {
  CHAT_CONTEXT_HEADER,
  chatContextForScope,
  currentAuthScope,
  notifyChatUnauthorized
} from './authScope';
import { CSRF_HEADER, UNAUTHORIZED_STATUS, csrfTokenForScope, newRequestId } from './client';

/** 本地协议/配置边界码；服务端稳定目录码原样上抛，不翻译。 */
export type WebchatStreamLocalCode =
  | 'INVALID_CONFIG'
  | 'FRAME_TOO_LARGE'
  | 'MALFORMED_FRAME'
  | 'SNAPSHOT_INCOMPLETE'
  | 'STREAM_INTERRUPTED'
  | 'NETWORK_ERROR'
  | 'AUTH_REQUIRED'
  | 'UNKNOWN';

/** 服务端游标错误响应里原样带出的窗口事实（TASK-061 的 `data`）。 */
export interface WebchatCursorWindow {
  after_seq?: number;
  earliest_seq?: number;
  through_seq?: number;
}

export class WebchatStreamError extends Error {
  /** 本地边界码或服务端稳定目录码（原样，不重写）。 */
  readonly code: string;
  /** HTTP 状态；本地协议/配置失败为 null。 */
  readonly status: number | null;
  /** 服务端游标窗口；非游标错误为 null。 */
  readonly cursor: WebchatCursorWindow | null;

  constructor(
    code: string,
    message: string,
    details: { status?: number | null; cursor?: WebchatCursorWindow | null } = {}
  ) {
    super(message);
    this.name = 'WebchatStreamError';
    this.code = code;
    this.status = details.status ?? null;
    this.cursor = details.cursor ?? null;
  }
}

const CURSOR_ERROR_CODES = new Set(['CURSOR_EXPIRED', 'CURSOR_AHEAD', 'CURSOR_RUN_MISMATCH']);
const AUTH_ERROR_CODES = new Set([
  'AUTH_REQUIRED',
  'UNAUTHORIZED',
  'CHAT_SESSION_EXPIRED',
  'CHAT_SESSION_REVOKED',
  'CHAT_TOKEN_INVALID',
  'CHAT_TOKEN_EXPIRED',
  'CHAT_TOKEN_REVOKED',
  'AUTH_CONTEXT_CHANGED'
]);
/** 可经重连/安全游标恢复的错误：网络中断、协议失败与有界过载。 */
const RETRYABLE_ERROR_CODES = new Set([
  'NETWORK_ERROR',
  'STREAM_INTERRUPTED',
  'FRAME_TOO_LARGE',
  'MALFORMED_FRAME',
  'SNAPSHOT_INCOMPLETE',
  'CHAT_AUTH_RATE_LIMITED',
  'RUN_CAPACITY_EXCEEDED',
  'RUN_QUEUE_TIMEOUT'
]);

export function webchatStreamErrorCode(error: unknown): string {
  return error instanceof WebchatStreamError ? error.code : 'UNKNOWN';
}

export function isWebchatStreamCursorError(error: unknown): boolean {
  return CURSOR_ERROR_CODES.has(webchatStreamErrorCode(error));
}

export function isWebchatStreamAuthError(error: unknown): boolean {
  if (!(error instanceof WebchatStreamError)) return false;
  return error.status === UNAUTHORIZED_STATUS || AUTH_ERROR_CODES.has(error.code);
}

export function isWebchatStreamRetryableError(error: unknown): boolean {
  if (!(error instanceof WebchatStreamError)) return false;
  if (error.status !== null && error.status >= 500) return true;
  return RETRYABLE_ERROR_CODES.has(error.code);
}

// ---------------------------------------------------------------------------------------------
// SSE 帧解析（增量、按字节）
// ---------------------------------------------------------------------------------------------

/** 一个完整的公开 SSE 帧；心跳/注释帧不产生帧。 */
export interface SseFrame {
  /** 帧的数值游标（`id:`）；心跳与无 id 帧为 null。 */
  cursor: number | null;
  /** 公开事件类型（`event:`）；缺省按 SSE 语义为 `message`。 */
  event: string;
  /** `data:` 行按换行拼接后的载荷。 */
  data: string;
}

export interface SseFrameParserOptions {
  /** 单帧字节上限（API-19 `transport.sse_event_max_bytes`，UTF-8 字节而非字符）。 */
  maxFrameBytes: number;
  /** 每个完整帧（空行收帧）的回调；心跳/注释帧不回调。 */
  onFrame: (frame: SseFrame) => void;
}

export interface SseFrameParser {
  push(chunk: Uint8Array): void;
  end(): void;
}

const LINE_FEED = 0x0a;
const CARRIAGE_RETURN = 0x0d;
const UTF8_DECODER = new TextDecoder('utf-8', { fatal: true });

function concatBytes(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (left.length === 0) return right;
  const merged: Uint8Array = new Uint8Array(left.length + right.length);
  merged.set(left, 0);
  merged.set(right, left.length);
  return merged;
}

function decodeLine(bytes: Uint8Array): string {
  try {
    return UTF8_DECODER.decode(bytes);
  } catch (error) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'SSE line is not valid UTF-8', {
      status: null
    });
  }
}

function parseCursor(value: string): number {
  if (!/^[0-9]+$/.test(value)) {
    throw new WebchatStreamError('MALFORMED_FRAME', `invalid SSE id: ${value}`);
  }
  const cursor = Number(value);
  if (!Number.isSafeInteger(cursor)) {
    throw new WebchatStreamError('MALFORMED_FRAME', `SSE id out of range: ${value}`);
  }
  return cursor;
}

/** 帧累积器：`id`/`event`/多行 `data` 与逐行累计字节数。 */
interface SseFrameAccumulator {
  bytes: number;
  cursor: number | null;
  event: string | null;
  dataLines: string[];
}

function resetAccumulator(accumulator: SseFrameAccumulator): void {
  accumulator.bytes = 0;
  accumulator.cursor = null;
  accumulator.event = null;
  accumulator.dataLines = [];
}

function emitAccumulatedFrame(
  accumulator: SseFrameAccumulator,
  onFrame: (frame: SseFrame) => void
): void {
  if (accumulator.cursor !== null || accumulator.event !== null || accumulator.dataLines.length > 0) {
    onFrame({
      cursor: accumulator.cursor,
      event: accumulator.event ?? 'message',
      data: accumulator.dataLines.join('\n')
    });
  }
  resetAccumulator(accumulator);
}

function applySseLine(
  accumulator: SseFrameAccumulator,
  line: string,
  onFrame: (frame: SseFrame) => void
): void {
  if (line === '') {
    emitAccumulatedFrame(accumulator, onFrame);
    return;
  }
  if (line.startsWith(':')) return; // 注释/心跳：不产生事件、不推进游标
  const separator = line.indexOf(':');
  const field = separator === -1 ? line : line.slice(0, separator);
  let value = separator === -1 ? '' : line.slice(separator + 1);
  if (value.startsWith(' ')) value = value.slice(1);
  if (field === 'id') accumulator.cursor = parseCursor(value);
  else if (field === 'event') accumulator.event = value;
  else if (field === 'data') accumulator.dataLines.push(value);
}

/**
 * 增量 SSE 帧解析器：按 `\n`/`\r\n` 行切分（多字节字符不可能被行边界切开，按行解码即可跨 chunk），
 * 空行收帧；帧字节数逐行累计，超界立即失败并清空缓冲（内存有界）。
 */
export function createSseFrameParser(options: SseFrameParserOptions): SseFrameParser {
  if (!Number.isInteger(options.maxFrameBytes) || options.maxFrameBytes < 1) {
    throw new WebchatStreamError('INVALID_CONFIG', 'sse_event_max_bytes must be a positive integer');
  }
  const maxFrameBytes = options.maxFrameBytes;
  const accumulator: SseFrameAccumulator = { bytes: 0, cursor: null, event: null, dataLines: [] };
  let pending: Uint8Array = new Uint8Array(0);

  const consumeLine = (lineBytes: Uint8Array, byteLength: number): void => {
    accumulator.bytes += byteLength;
    const line = decodeLine(lineBytes);
    if (accumulator.bytes > maxFrameBytes) {
      pending = new Uint8Array(0);
      resetAccumulator(accumulator);
      throw new WebchatStreamError(
        'FRAME_TOO_LARGE',
        `SSE frame exceeds sse_event_max_bytes=${maxFrameBytes}`
      );
    }
    applySseLine(accumulator, line, options.onFrame);
  };

  return {
    push(chunk: Uint8Array): void {
      pending = concatBytes(pending, chunk);
      let start = 0;
      for (let index = 0; index < pending.length; index += 1) {
        if (pending[index] !== LINE_FEED) continue;
        const lineEnd = index > start && pending[index - 1] === CARRIAGE_RETURN ? index - 1 : index;
        consumeLine(pending.subarray(start, lineEnd), index - start + 1);
        start = index + 1;
      }
      pending = pending.subarray(start);
    },
    end(): void {
      // EOF 时未收齐的帧丢弃：游标未推进，重连会重投；绝不把半帧当完整事件。
      pending = new Uint8Array(0);
      resetAccumulator(accumulator);
    }
  };
}

// ---------------------------------------------------------------------------------------------
// 分片收集与回复状态
// ---------------------------------------------------------------------------------------------

export type ReplyIgnoreReason =
  | 'DUPLICATE_SEQ'
  | 'DUPLICATE_PART'
  | 'STALE_GENERATION'
  | 'UNKNOWN_EVENT';

/** 已应用结果；`ignored` 显式说明被去重/被 fence 的帧（不静默）。 */
export type ReplyStreamUpdate =
  | { kind: 'delta'; seq: number; replyGeneration: number; delta: string; text: string }
  | {
      kind: 'snapshot-part';
      seq: number;
      replyGeneration: number;
      partIndex: number;
      received: number;
      partCount: number;
    }
  | { kind: 'snapshot'; seq: number; replyGeneration: number; text: string; partCount: number }
  | { kind: 'stage'; seq: number; data: WebchatExecutionStatus }
  | { kind: 'terminal'; seq: number; status: RunStatus }
  | { kind: 'error'; seq: number; code: string; retryable: boolean }
  | { kind: 'confirmation'; seq: number; data: WebchatConfirmation }
  | { kind: 'artifact'; seq: number; data: WebchatAttachment }
  | { kind: 'checkpoint'; cursor: number }
  | { kind: 'ignored'; seq: number; reason: ReplyIgnoreReason };

export interface ReplyStreamState {
  runId: string;
  /** 已应用正文（delta 追加 / snapshot 整组替换之后）。 */
  text: string;
  /** 当前回复代；尚无正文时为 null（中途加入的流以首个正文事件的代号为准）。 */
  replyGeneration: number | null;
  /** 已应用水位：所有 ≤ 它的公开事件都已应用。 */
  appliedCursor: number;
  /** 已扫描安全水位（checkpoint；不跨未齐组）。 */
  scannedCursor: number;
  /** 未齐 snapshot 组的待收片数（0 = 无未齐组）。 */
  pendingParts: number;
}

export interface ReplyStreamCollectorOptions {
  runId: string;
  /** 已知的已应用水位（重连/恢复续传；必须真实应用过，不能拿历史页水位冒充）。 */
  afterSeq?: number;
  /** 已知的已应用正文（同一订阅重开时恢复；缺省为空，由服务端安全快照/增量重建）。 */
  text?: string;
  /** 已知的回复代（与 `text` 同时恢复；缺省为 null，以首个正文事件的代号为准）。 */
  replyGeneration?: number | null;
}

export interface ReplyStreamCollector {
  state(): ReplyStreamState;
  /** 安全续传游标：已应用 + 已扫描水位，且不跨越未齐组。 */
  resumeCursor(): number;
  /** 消费一个公开帧；协议违规显式抛 `WebchatStreamError`。 */
  apply(frame: SseFrame): ReplyStreamUpdate;
  /** 断流：丢弃未齐组（游标保持安全水位，重连由服务端重投整组）。 */
  disconnect(): void;
}

interface PendingSnapshotGroup {
  eventId: string;
  seq: number;
  replyGeneration: number;
  partCount: number;
  parts: Map<number, string>;
}

const EXECUTION_STAGES = new Set<string>([
  'WAITING_RUNTIME',
  'WAITING_MODEL',
  'RUNNING',
  'WAITING_TOOL',
  'WAITING_INPUT',
  'COMPLETED',
  'FAILED',
  'CANCELLED'
]);
const TERMINAL_STATUSES = new Set<string>(['COMPLETED', 'FAILED', 'CANCELLED']);

function parseJsonObject(source: string): Record<string, unknown> {
  let decoded: unknown;
  try {
    decoded = JSON.parse(source);
  } catch (error) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'SSE data is not valid JSON');
  }
  if (typeof decoded !== 'object' || decoded === null || Array.isArray(decoded)) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'SSE data must be a JSON object');
  }
  return decoded as Record<string, unknown>;
}

function readObject(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new WebchatStreamError('MALFORMED_FRAME', `payload.${key} must be an object`);
  }
  return value as Record<string, unknown>;
}

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  if (typeof value !== 'string' || value === '') {
    throw new WebchatStreamError('MALFORMED_FRAME', `payload.${key} must be a non-empty string`);
  }
  return value;
}

function readCount(source: Record<string, unknown>, key: string, minimum: number): number {
  const value = source[key];
  if (!Number.isInteger(value) || (value as number) < minimum) {
    throw new WebchatStreamError('MALFORMED_FRAME', `payload.${key} must be an integer >= ${minimum}`);
  }
  return value as number;
}

function readNullableString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string') {
    throw new WebchatStreamError('MALFORMED_FRAME', `payload.${key} must be a string or null`);
  }
  return value;
}

function readExecutionStatus(source: Record<string, unknown>): WebchatExecutionStatus {
  const stage = readString(source, 'stage');
  if (!EXECUTION_STAGES.has(stage)) {
    throw new WebchatStreamError('MALFORMED_FRAME', `unknown execution stage: ${stage}`);
  }
  return {
    stage: stage as WebchatExecutionStatus['stage'],
    reason: readNullableString(source, 'reason'),
    queued_at: readNullableString(source, 'queued_at'),
    deadline_at: readNullableString(source, 'deadline_at'),
    started_at: readNullableString(source, 'started_at')
  };
}

interface ParsedEvent {
  eventId: string;
  seq: number;
  data: Record<string, unknown>;
}

function parseEvent(frame: SseFrame): ParsedEvent {
  const envelope = parseJsonObject(frame.data);
  const type = readString(envelope, 'type');
  if (type !== frame.event) {
    throw new WebchatStreamError('MALFORMED_FRAME', `event type mismatch: ${frame.event} vs ${type}`);
  }
  const seq = readCount(envelope, 'seq', 1);
  if (frame.cursor !== null && frame.cursor !== seq) {
    throw new WebchatStreamError('MALFORMED_FRAME', `SSE id ${frame.cursor} != payload seq ${seq}`);
  }
  return { eventId: readString(envelope, 'event_id'), seq, data: readObject(envelope, 'data') };
}

function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** 公开事件收集器的可变状态：正文分代、分片整组、去重与安全游标只在这里定义一次。 */
interface ReplyCollectorState {
  runId: string;
  text: string;
  generation: number | null;
  applied: number;
  scanned: number;
  pending: PendingSnapshotGroup | null;
}

function collectorResumeCursor(state: ReplyCollectorState): number {
  const ceiling =
    state.pending === null ? state.scanned : Math.min(state.scanned, state.pending.seq - 1);
  return Math.max(state.applied, ceiling);
}

function collectorSnapshot(state: ReplyCollectorState): ReplyStreamState {
  return {
    runId: state.runId,
    text: state.text,
    replyGeneration: state.generation,
    appliedCursor: state.applied,
    scannedCursor: state.scanned,
    pendingParts: state.pending === null ? 0 : state.pending.partCount - state.pending.parts.size
  };
}

function applyDeltaEvent(state: ReplyCollectorState, parsed: ParsedEvent): ReplyStreamUpdate {
  const replyGeneration = readCount(parsed.data, 'reply_generation', 0);
  if (state.generation !== null && replyGeneration < state.generation) {
    return { kind: 'ignored', seq: parsed.seq, reason: 'STALE_GENERATION' };
  }
  if (state.generation !== null && replyGeneration > state.generation) {
    throw new WebchatStreamError(
      'MALFORMED_FRAME',
      `reply_generation ${replyGeneration} without a replacing snapshot`
    );
  }
  const delta = readString(parsed.data, 'text');
  state.generation = replyGeneration;
  state.text += delta;
  state.applied = parsed.seq;
  return { kind: 'delta', seq: parsed.seq, replyGeneration, delta, text: state.text };
}

function completeSnapshotGroup(
  state: ReplyCollectorState,
  group: PendingSnapshotGroup
): ReplyStreamUpdate {
  const pieces: string[] = [];
  for (let index = 0; index < group.partCount; index += 1) {
    const piece = group.parts.get(index);
    if (piece === undefined) {
      throw new WebchatStreamError('SNAPSHOT_INCOMPLETE', 'snapshot group lost a part');
    }
    pieces.push(piece);
  }
  const joined = pieces.join('');
  state.generation = group.replyGeneration;
  state.text = joined;
  state.applied = group.seq;
  state.pending = null;
  return {
    kind: 'snapshot',
    seq: group.seq,
    replyGeneration: group.replyGeneration,
    text: joined,
    partCount: group.partCount
  };
}

function applySnapshotPartEvent(
  state: ReplyCollectorState,
  parsed: ParsedEvent
): ReplyStreamUpdate {
  const replyGeneration = readCount(parsed.data, 'reply_generation', 0);
  const partIndex = readCount(parsed.data, 'part_index', 0);
  const partCount = readCount(parsed.data, 'part_count', 1);
  if (partIndex >= partCount) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'part_index must be less than part_count');
  }
  if (state.generation !== null && replyGeneration < state.generation) {
    return { kind: 'ignored', seq: parsed.seq, reason: 'STALE_GENERATION' };
  }
  const current = state.pending;
  if (
    current !== null &&
    (current.eventId !== parsed.eventId ||
      current.seq !== parsed.seq ||
      current.replyGeneration !== replyGeneration)
  ) {
    state.pending = null;
    throw new WebchatStreamError(
      'SNAPSHOT_INCOMPLETE',
      `snapshot group at seq ${parsed.seq} interleaved with another event`
    );
  }
  if (current !== null && current.partCount !== partCount) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'part_count changed within a snapshot group');
  }
  const group: PendingSnapshotGroup =
    current ??
    ({
      eventId: parsed.eventId,
      seq: parsed.seq,
      replyGeneration,
      partCount,
      parts: new Map()
    } satisfies PendingSnapshotGroup);
  state.pending = group;
  if (group.parts.has(partIndex)) {
    return { kind: 'ignored', seq: parsed.seq, reason: 'DUPLICATE_PART' };
  }
  group.parts.set(partIndex, readString(parsed.data, 'text'));
  if (group.parts.size < group.partCount) {
    return {
      kind: 'snapshot-part',
      seq: group.seq,
      replyGeneration: group.replyGeneration,
      partIndex,
      received: group.parts.size,
      partCount: group.partCount
    };
  }
  return completeSnapshotGroup(state, group);
}

function applyDataFrameEvent(
  state: ReplyCollectorState,
  frame: SseFrame,
  parsed: ParsedEvent
): ReplyStreamUpdate {
  if (parsed.seq <= state.applied) {
    return { kind: 'ignored', seq: parsed.seq, reason: 'DUPLICATE_SEQ' };
  }
  if (state.pending !== null && frame.event !== 'message.snapshot') {
    state.pending = null;
    throw new WebchatStreamError(
      'SNAPSHOT_INCOMPLETE',
      `snapshot group at seq ${parsed.seq} was truncated`
    );
  }
  if (frame.event === 'message.delta') return applyDeltaEvent(state, parsed);
  if (frame.event === 'message.snapshot') return applySnapshotPartEvent(state, parsed);
  if (frame.event === 'run.stage') {
    const data = readExecutionStatus(parsed.data);
    state.applied = parsed.seq;
    return { kind: 'stage', seq: parsed.seq, data };
  }
  if (frame.event === 'run.terminal') {
    const status = readString(parsed.data, 'status');
    if (!TERMINAL_STATUSES.has(status)) {
      throw new WebchatStreamError('MALFORMED_FRAME', `unknown terminal status: ${status}`);
    }
    state.applied = parsed.seq;
    return { kind: 'terminal', seq: parsed.seq, status: status as RunStatus };
  }
  if (frame.event === 'run.error') {
    const code = readString(parsed.data, 'code');
    const retryable = parsed.data['retryable'];
    if (typeof retryable !== 'boolean') {
      throw new WebchatStreamError('MALFORMED_FRAME', 'run.error.retryable must be a boolean');
    }
    state.applied = parsed.seq;
    return { kind: 'error', seq: parsed.seq, code, retryable };
  }
  if (frame.event === 'confirmation.required') {
    const data = readConfirmation(parsed.data);
    state.applied = parsed.seq;
    return { kind: 'confirmation', seq: parsed.seq, data };
  }
  if (frame.event === 'artifact.created') {
    const data = readAttachment(parsed.data);
    state.applied = parsed.seq;
    return { kind: 'artifact', seq: parsed.seq, data };
  }
  throw new WebchatStreamError('MALFORMED_FRAME', `unknown public event type: ${frame.event}`);
}

function applyCheckpointFrame(state: ReplyCollectorState, frame: SseFrame): ReplyStreamUpdate {
  const envelope = parseJsonObject(frame.data);
  const type = readString(envelope, 'type');
  if (type !== 'checkpoint') {
    throw new WebchatStreamError('MALFORMED_FRAME', `event type mismatch: checkpoint vs ${type}`);
  }
  const seq = readCount(envelope, 'seq', 0);
  if (frame.cursor !== null && frame.cursor !== seq) {
    throw new WebchatStreamError('MALFORMED_FRAME', `SSE id ${frame.cursor} != checkpoint seq ${seq}`);
  }
  const ceiling = state.pending === null ? seq : Math.min(seq, state.pending.seq - 1);
  state.scanned = Math.max(state.scanned, ceiling);
  return { kind: 'checkpoint', cursor: state.scanned };
}

function applyReplyFrame(state: ReplyCollectorState, frame: SseFrame): ReplyStreamUpdate {
  if (frame.event === 'checkpoint') return applyCheckpointFrame(state, frame);
  return applyDataFrameEvent(state, frame, parseEvent(frame));
}

/** 公开事件收集器：消费解析后的帧，产出可应用的更新与安全续传游标。 */
export function createReplyStreamCollector(
  options: ReplyStreamCollectorOptions
): ReplyStreamCollector {
  const runId = options.runId.trim();
  const afterSeq = options.afterSeq ?? 0;
  const text = options.text ?? '';
  const generation = options.replyGeneration ?? null;
  if (
    runId === '' ||
    !Number.isInteger(afterSeq) ||
    afterSeq < 0 ||
    typeof text !== 'string' ||
    (generation !== null && (!Number.isInteger(generation) || generation < 0))
  ) {
    throw new WebchatStreamError('INVALID_CONFIG', 'runId/afterSeq/text/replyGeneration must be valid');
  }
  const state: ReplyCollectorState = {
    runId,
    text,
    generation,
    applied: afterSeq,
    scanned: afterSeq,
    pending: null
  };
  return {
    state: () => collectorSnapshot(state),
    resumeCursor: () => collectorResumeCursor(state),
    apply: (frame: SseFrame) => applyReplyFrame(state, frame),
    disconnect: () => {
      state.pending = null;
    }
  };
}

function readConfirmation(source: Record<string, unknown>): WebchatConfirmation {
  const options = source['options'];
  if (!Array.isArray(options) || options.length === 0) {
    throw new WebchatStreamError('MALFORMED_FRAME', 'confirmation options must be a non-empty array');
  }
  const parsed = options.map((option) => {
    if (typeof option !== 'object' || option === null) {
      throw new WebchatStreamError('MALFORMED_FRAME', 'confirmation option must be an object');
    }
    const record = option as Record<string, unknown>;
    return { value: readString(record, 'value'), label: readString(record, 'label') };
  });
  return {
    submission_id: readString(source, 'submission_id'),
    source_event_id: readString(source, 'source_event_id'),
    prompt: readString(source, 'prompt'),
    options: parsed,
    expires_at: readString(source, 'expires_at')
  };
}

function readAttachment(source: Record<string, unknown>): WebchatAttachment {
  const kind = readString(source, 'kind');
  if (kind !== 'IMAGE' && kind !== 'DOCUMENT' && kind !== 'OTHER') {
    throw new WebchatStreamError('MALFORMED_FRAME', `unknown attachment kind: ${kind}`);
  }
  const available = source['available'];
  if (typeof available !== 'boolean') {
    throw new WebchatStreamError('MALFORMED_FRAME', 'artifact.created.available must be a boolean');
  }
  return {
    artifact_id: readString(source, 'artifact_id'),
    kind,
    available,
    media_type: readString(source, 'media_type'),
    size: readCount(source, 'size', 0),
    filename: readNullableString(source, 'filename'),
    checksum: readString(source, 'checksum')
  };
}

// ---------------------------------------------------------------------------------------------
// 传输与重连
// ---------------------------------------------------------------------------------------------

interface FrozenTransport {
  transportRevision: string;
  sseEventMaxBytes: number;
  reconnectBaseMs: number;
  reconnectCapMs: number;
  reconnectMaxAttempts: number;
}

function freezeTransport(config: WebchatClientConfig): FrozenTransport {
  const transport = config.transport;
  const values = [
    transport.sse_event_max_bytes,
    transport.reconnect_base_ms,
    transport.reconnect_cap_ms,
    transport.reconnect_max_attempts
  ];
  const revision = config.transport_revision;
  if (
    typeof revision !== 'string' ||
    revision.trim() === '' ||
    values.some((value) => !Number.isInteger(value) || value < 0) ||
    transport.sse_event_max_bytes < 1
  ) {
    throw new WebchatStreamError('INVALID_CONFIG', 'client transport projection is invalid');
  }
  return {
    transportRevision: revision,
    sseEventMaxBytes: transport.sse_event_max_bytes,
    reconnectBaseMs: transport.reconnect_base_ms,
    reconnectCapMs: Math.max(transport.reconnect_base_ms, transport.reconnect_cap_ms),
    reconnectMaxAttempts: transport.reconnect_max_attempts
  };
}

/**
 * 第 `attempt` 次重连的退避（1 起）：指数增长 + equal jitter（半固定 + 半随机），
 * 上限为配置 `reconnect_cap_ms`。抖动源可注入，便于确定性验证。
 */
export function reconnectDelayMs(
  attempt: number,
  transport: Pick<WebchatClientTransport, 'reconnect_base_ms' | 'reconnect_cap_ms'>,
  random: () => number = Math.random
): number {
  if (!Number.isInteger(attempt) || attempt < 1) {
    throw new WebchatStreamError('INVALID_CONFIG', 'reconnect attempt must be a positive integer');
  }
  const ceiling = Math.min(
    transport.reconnect_base_ms * 2 ** (attempt - 1),
    Math.max(transport.reconnect_base_ms, transport.reconnect_cap_ms)
  );
  const jitter = Math.min(Math.max(random(), 0), 1);
  return Math.round(ceiling / 2 + (ceiling / 2) * jitter);
}

function reconnectDelayFor(
  attempt: number,
  transport: FrozenTransport,
  random?: () => number
): number {
  return reconnectDelayMs(
    attempt,
    {
      reconnect_base_ms: transport.reconnectBaseMs,
      reconnect_cap_ms: transport.reconnectCapMs
    },
    random
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function readCursorWindow(value: unknown): WebchatCursorWindow | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const window: WebchatCursorWindow = {};
  for (const key of ['after_seq', 'earliest_seq', 'through_seq'] as const) {
    const candidate = record[key];
    if (typeof candidate === 'number' && Number.isFinite(candidate)) window[key] = candidate;
  }
  return Object.keys(window).length > 0 ? window : null;
}

async function streamErrorFromResponse(response: Response): Promise<WebchatStreamError> {
  let envelope: { code?: unknown; data?: unknown } | null = null;
  try {
    envelope = (await response.json()) as { code?: unknown; data?: unknown };
  } catch (error) {
    envelope = null; // 错误体不可解析（代理 HTML/空体）：按状态码显式失败
  }
  const code =
    typeof envelope?.code === 'string' && envelope.code !== ''
      ? envelope.code
      : response.status === UNAUTHORIZED_STATUS
        ? 'UNAUTHORIZED'
        : 'UNKNOWN';
  return new WebchatStreamError(code, `stream request failed with status ${response.status}`, {
    status: response.status,
    cursor: readCursorWindow(envelope?.data)
  });
}

function eventStreamUrl(runId: string, cursor: number): string {
  const query = cursor > 0 ? `?after_seq=${cursor}` : '';
  return `/api/v1/webchat/runs/${encodeURIComponent(runId)}/events${query}`;
}

function eventStreamHeaders(cursor: number): Record<string, string> {
  const scope = currentAuthScope();
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'X-Locale': currentLocale(),
    'X-Request-Id': newRequestId()
  };
  const csrf = csrfTokenForScope(scope);
  if (csrf) headers[CSRF_HEADER] = csrf;
  const context = chatContextForScope(scope);
  if (context) headers[CHAT_CONTEXT_HEADER] = context;
  if (cursor > 0) headers['Last-Event-ID'] = String(cursor);
  return headers;
}

export interface WebchatReplyStreamOptions {
  runId: string;
  /** API-19 投影：连接创建时冻结其传输参数与 `transport_revision`。 */
  config: WebchatClientConfig;
  /** 已知的已应用水位（刷新/重连续传；必须真实应用过）。 */
  afterSeq?: number;
  /** 已知的已应用正文（同一订阅重开时恢复；缺省由服务端安全快照/增量重建）。 */
  text?: string;
  /** 已知的回复代（与 `text` 同时恢复；缺省以首个正文事件的代号为准）。 */
  replyGeneration?: number | null;
  /** 调用方持有的 AbortSignal（切换会话/卸载）；与 close() 等价。 */
  signal?: AbortSignal;
  onUpdate?: (update: ReplyStreamUpdate) => void;
  /**
   * 每次连接尝试（首连与重连都是 GET）的显式上报：编排层据此展示连接状态与重连次数，
   * 退避/次数仍只由本层按配置投影计算。
   */
  onAttempt?: (attempt: WebchatConnectionAttempt) => void;
  /** 抖动源（默认 Math.random）；测试可注入确定值。 */
  random?: () => number;
}

/** 一次连接尝试：序号/连续重连次数/本次续传游标（只重连 GET，不重发 POST）。 */
export interface WebchatConnectionAttempt {
  /** 本次订阅内的连接序号（1 起；>1 即断流后的重连）。 */
  attempt: number;
  /** 自上次有进展以来的连续重连次数（0 = 首连或上一连接已有进展）。 */
  retries: number;
  /** 本次连接使用的续传游标（排他；0 = 无本地游标，从起点/服务端安全快照恢复）。 */
  cursor: number;
}

export type WebchatReplyStop =
  | { kind: 'terminal'; status: RunStatus }
  | { kind: 'run-error'; code: string; retryable: boolean }
  | { kind: 'closed' }
  | { kind: 'cursor-recovery'; error: WebchatStreamError }
  | { kind: 'auth-required'; error: WebchatStreamError }
  | { kind: 'failed'; error: WebchatStreamError };

export interface WebchatReplyStream {
  readonly runId: string;
  /** 创建时绑定的传输版本（活跃连接不因新配置中途换规则）。 */
  readonly transportRevision: string;
  /** 订阅结束原因（正常关闭/终态/需恢复/失败都显式）。 */
  readonly done: Promise<WebchatReplyStop>;
  state(): ReplyStreamState;
  cursor(): number;
  close(): void;
}

function asStreamError(error: unknown): WebchatStreamError {
  return error instanceof WebchatStreamError
    ? error
    : new WebchatStreamError('NETWORK_ERROR', 'stream connection failed');
}

/** 连接失败的恢复裁决：返回立即结束的原因；可经重连恢复时返回 null。 */
function stopForFailure(error: unknown): WebchatReplyStop | null {
  const streamError = asStreamError(error);
  if (isWebchatStreamAuthError(streamError)) {
    notifyChatUnauthorized();
    return { kind: 'auth-required', error: streamError };
  }
  if (isWebchatStreamCursorError(streamError)) {
    return { kind: 'cursor-recovery', error: streamError };
  }
  if (!isWebchatStreamRetryableError(streamError)) {
    return { kind: 'failed', error: streamError };
  }
  return null;
}

function interruptedError(connections: number): WebchatStreamError {
  return new WebchatStreamError(
    'STREAM_INTERRUPTED',
    `stream ended without terminal after ${connections + 1} connections`
  );
}

interface ConnectionOutcome {
  /** 流内已到达的终态/失败事件；EOF 为 null（EOF 不当完成）。 */
  stop: WebchatReplyStop | null;
  /** 本次连接是否推进过状态（用于重置重连预算）。 */
  progress: boolean;
}

interface ReplyAttempt extends ConnectionOutcome {
  /** 需要重连时保留的最近错误；成功连接为 null。 */
  error: WebchatStreamError | null;
}

async function attemptReplyConnection(
  runId: string,
  transport: FrozenTransport,
  collector: ReplyStreamCollector,
  signal: AbortSignal,
  onUpdate: ((update: ReplyStreamUpdate) => void) | undefined
): Promise<ReplyAttempt> {
  try {
    const outcome = await consumeConnection(runId, transport, collector, signal, onUpdate);
    return { stop: outcome.stop, progress: outcome.progress, error: null };
  } catch (error) {
    const stop = stopForFailure(error);
    return { stop, progress: false, error: stop === null ? asStreamError(error) : null };
  }
}

async function consumeConnection(
  runId: string,
  transport: FrozenTransport,
  collector: ReplyStreamCollector,
  signal: AbortSignal,
  onUpdate: ((update: ReplyStreamUpdate) => void) | undefined
): Promise<ConnectionOutcome> {
  const cursor = collector.resumeCursor();
  const response = await fetch(eventStreamUrl(runId, cursor), {
    method: 'GET',
    headers: eventStreamHeaders(cursor),
    signal
  });
  if (!response.ok) throw await streamErrorFromResponse(response);
  const body = response.body;
  if (body === null) {
    throw new WebchatStreamError('NETWORK_ERROR', 'stream response has no body');
  }
  let inStreamStop: WebchatReplyStop | null = null;
  let progress = false;
  const parser = createSseFrameParser({
    maxFrameBytes: transport.sseEventMaxBytes,
    onFrame: (frame) => {
      const update = collector.apply(frame);
      progress = true;
      onUpdate?.(update);
      if (update.kind === 'terminal') inStreamStop = { kind: 'terminal', status: update.status };
      else if (update.kind === 'error') {
        inStreamStop = { kind: 'run-error', code: update.code, retryable: update.retryable };
      }
    }
  });
  const reader = body.getReader();
  while (inStreamStop === null) {
    const chunk = await reader.read();
    if (chunk.done) break;
    parser.push(chunk.value);
  }
  if (inStreamStop === null) {
    parser.end();
    collector.disconnect();
  } else {
    try {
      await reader.cancel();
    } catch (error) {
      // 已决定停止：取消失败不改变结论（连接由 AbortSignal/服务端回收）
    }
  }
  return { stop: inStreamStop, progress };
}

/** 重连循环上下文：连接尝试、退避与结束裁决的显式依赖。 */
interface ReplyLoopContext {
  runId: string;
  transport: FrozenTransport;
  collector: ReplyStreamCollector;
  signal: AbortSignal;
  onUpdate: ((update: ReplyStreamUpdate) => void) | undefined;
  onAttempt: ((attempt: WebchatConnectionAttempt) => void) | undefined;
  random: (() => number) | undefined;
  /** 订阅是否已被调用方结束（close/abort）；循环据此退出。 */
  stopped: () => boolean;
  finish: (stop: WebchatReplyStop) => void;
}

async function runReplyLoop(context: ReplyLoopContext): Promise<void> {
  let reconnects = 0;
  let attempts = 0;
  let lastError: WebchatStreamError | null = null;
  while (!context.stopped()) {
    attempts += 1;
    context.onAttempt?.({
      attempt: attempts,
      retries: reconnects,
      cursor: context.collector.resumeCursor()
    });
    const attempt = await attemptReplyConnection(
      context.runId,
      context.transport,
      context.collector,
      context.signal,
      context.onUpdate
    );
    if (context.stopped()) return;
    if (attempt.stop !== null) {
      context.finish(attempt.stop);
      return;
    }
    if (attempt.error === null) {
      if (attempt.progress) reconnects = 0;
      lastError = null;
    } else {
      lastError = attempt.error;
    }
    if (reconnects >= context.transport.reconnectMaxAttempts) {
      context.finish({ kind: 'failed', error: lastError ?? interruptedError(reconnects) });
      return;
    }
    reconnects += 1;
    await sleep(reconnectDelayFor(reconnects, context.transport, context.random));
  }
}

/** 公开回复订阅：GET 长流 + 解析/收集 + 按配置投影有界重连（不重发任何 POST）。 */
export function openReplyStream(options: WebchatReplyStreamOptions): WebchatReplyStream {
  const runId = options.runId.trim();
  if (runId === '') {
    throw new WebchatStreamError('INVALID_CONFIG', 'runId is required');
  }
  const transport = freezeTransport(options.config);
  const collector = createReplyStreamCollector({
    runId,
    afterSeq: options.afterSeq ?? 0,
    text: options.text,
    replyGeneration: options.replyGeneration
  });
  const controller = new AbortController();
  let stop: WebchatReplyStop | null = null;
  let resolveDone: (value: WebchatReplyStop) => void = () => undefined;
  const done = new Promise<WebchatReplyStop>((resolve) => {
    resolveDone = resolve;
  });
  const finish = (value: WebchatReplyStop): void => {
    if (stop !== null) return;
    stop = value;
    controller.abort();
    resolveDone(value);
  };

  if (options.signal !== undefined) {
    if (options.signal.aborted) finish({ kind: 'closed' });
    else options.signal.addEventListener('abort', () => finish({ kind: 'closed' }), { once: true });
  }
  void runReplyLoop({
    runId,
    transport,
    collector,
    signal: controller.signal,
    onUpdate: options.onUpdate,
    onAttempt: options.onAttempt,
    random: options.random,
    stopped: () => stop !== null,
    finish
  });

  return {
    runId,
    transportRevision: transport.transportRevision,
    done,
    state: () => collector.state(),
    cursor: () => collector.resumeCursor(),
    close: () => finish({ kind: 'closed' })
  };
}
