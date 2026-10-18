/**
 * 输入区与发送幂等状态（design frontend §3.3 `ChatComposer`、§3.4 组件接口契约、§3.5 `pendingSend`、
 * §3.6 发送/回复/附件；TASK-081）。
 *
 * 这一层是"当前输入 + 一次发送尝试"的唯一状态机（框架无关的控制器 + React 绑定）：
 *
 * 1. **文件用 state**：附件选择（`File` 对象、逐件上传状态、暂存身份）只存在于内存状态里，随输入区
 *    一起被 `useSyncExternalStore` 订阅渲染；不塞未注册 Form 字段、不用 `FormData` 收集输入、不落
 *    持久存储（真实字节由模块 service 按 TASK-054 的 multipart 面上送，这里只传 `Blob`）。
 * 2. **预检先于 IO**：文本按 UTF-8 字节（`text_max_bytes`）、附件按数量（`upload_max_per_message`）与
 *    单件大小（`upload_max_bytes`）在发请求前预检；限额只来自 API-19 投影（`limits()`），本文件不持有
 *    任何默认值，也不替后端做类型/授权裁决（服务端稳定码原样展示）。
 * 3. **一次尝试一个消息号**：`messageId` 在尝试创建时生成一次，`Idempotency-Key` 就是它（原样透传）；
 *    尝试固定 `conversationId`（发起后切换视图不改投）。提交发出后 payload 冻结：`send`/`retry` 都
 *    原样重放同一消息号/幂等键/文本/附件身份——不换号、不重传已暂存附件（过期暂存不破坏已受理重放）。
 * 4. **202 前是待受理**：提交在飞时 `phase=PENDING`，成功前不清输入、不显示伪成功；202 落地才清空输入
 *    并保留服务端回执（线程/订阅接缝由 TASK-082/093 消费）。
 * 5. **视图与身份边界**：视图信号（TASK-078 的 `viewSignal()`）中止在途发送——已发出的请求可能已被
 *    受理，所以结果是"未知"、原身份保留待重放；尝试按会话隔离，跨会话重试显式失败；身份边界
 *    （TASK-074 的 `registerIdentityCleanup`）清空尝试与输入。
 *
 * 本文件不发 HTTP（HTTP 都在模块 service 里）、不拼装传输头、不读 Cookie、不落持久存储、不打日志、
 * 不自带定时器（过期是本地判定，在下一次动作时刷新），也不持有任何文案键。
 */

import { useEffect, useMemo, useSyncExternalStore } from 'react';

import { newRequestId } from '../../../api/client';

import { ClientConfigError } from '../clientConfig';
import { webchatErrorCode } from '../services/webchatApi';
import type {
  WebchatMessageAccepted,
  WebchatRequestOptions,
  WebchatSubmitInput,
  WebchatUpload,
  WebchatWriteOptions
} from '../types';

/** 附件逐件状态：选择后待上传 → 上传中 → 已暂存 / 上传失败。 */
export type ComposerFileStatus = 'READY' | 'UPLOADING' | 'UPLOADED' | 'FAILED';

/** 输入区附件条目：只放展示与暂存身份，不携带存储键（真实字节留在运行时句柄里）。 */
export interface ComposerAttachment {
  /** 选择时分配的本地身份（重试沿用同一条目，不按数组下标定位）。 */
  localId: string;
  filename: string;
  size: number;
  mediaType: string;
  status: ComposerFileStatus;
  /** API-10 的暂存身份；未上传/失败为 null。 */
  uploadId: string | null;
  /** 服务端 `expires_at` 原样保留（本地只做提示，服务端仍是权威）。 */
  expiresAt: string | null;
  errorCode: string | null;
}

/** 发送阶段：待受理（`PENDING`）与上传中（`STAGING`）分开，成功前不清输入。 */
export type PendingSendPhase = 'IDLE' | 'STAGING' | 'PENDING' | 'FAILED';

/** 一次发送尝试：身份与 payload 在提交后冻结，重试原样重放。 */
export interface PendingSendAttempt {
  conversationId: string;
  messageId: string;
  idempotencyKey: string;
  text: string;
  uploadIds: string[];
  /** 首次提交已发出：此后 payload 冻结（重试只重放，不换号、不改附件身份）。 */
  submitted: boolean;
  attachments: ComposerAttachment[];
}

/** 输入区快照：组件只消费这一份事实（当前会话的输入、尝试、阶段与稳定码）。 */
export interface PendingSendSnapshot {
  conversationId: string | null;
  phase: PendingSendPhase;
  /** 当前会话可见的尝试；其它会话的尝试被隔离（不跨会话展示/改投）。 */
  attempt: PendingSendAttempt | null;
  text: string;
  attachments: ComposerAttachment[];
  /** 输入冻结：在途发送或已提交待重试（UI 据此禁用编辑，不静默改 payload）。 */
  inputFrozen: boolean;
  /** 输入侧预检失败（发请求前）。 */
  inputErrorCode: string | null;
  /** 发送/暂存失败（本地稳定码或服务端目录码）。 */
  errorCode: string | null;
  /** 响应未知：重试必须沿用同一消息号（已可能被服务端受理）。 */
  outcomeUnknown: boolean;
  /** 已暂存但本地判定过期的上传身份（只提示，不改写身份、不重传）。 */
  expiredUploadIds: string[];
  /** 最近一次 202 回执（线程/订阅接缝）。 */
  accepted: WebchatMessageAccepted | null;
}

/** 本地稳定码：发请求前的预检/状态失败（不用服务端 4xx 表达前端缺参）。 */
export type PendingSendErrorCode =
  | 'SEND_ENTRY_REQUIRED'
  | 'SEND_CONVERSATION_REQUIRED'
  | 'SEND_EMPTY'
  | 'SEND_TEXT_TOO_LONG'
  | 'SEND_TOO_MANY_FILES'
  | 'SEND_FILE_TOO_LARGE'
  | 'SEND_VIEW_CHANGED'
  | 'SEND_ABORTED'
  | 'SEND_ATTACHMENT_UPLOAD_FAILED';

/** 动态限额端口：只取 API-19 投影里输入区需要的三个上限（`ClientConfigService` 结构满足）。 */
export interface PendingSendLimitsPort {
  limits(): ComposerAttachmentPolicy;
}

/** 输入区限额（唯一来源是 API-19 投影；本文件不复制默认值）。 */
export interface ComposerAttachmentPolicy {
  readonly text_max_bytes: number;
  readonly upload_max_bytes: number;
  readonly upload_max_per_message: number;
}

/** 写面端口：只取 `WebchatClient` 的上传与提交（结构上没有任何读/取消/认证能力）。 */
export interface PendingSendClientPort {
  upload(entryId: string, file: Blob, options?: WebchatRequestOptions): Promise<WebchatUpload>;
  submit(
    entryId: string,
    input: WebchatSubmitInput,
    options: WebchatWriteOptions
  ): Promise<WebchatMessageAccepted>;
}

/** 视图端口：当前所选会话 + 视图请求信号（TASK-078 控制器结构满足）。 */
export interface PendingSendViewPort {
  conversationId(): string | null;
  viewSignal(): AbortSignal;
}

/** 身份边界端口：退出/切主体清空尝试与输入（TASK-074 认证状态机结构满足）。 */
export interface PendingSendIdentityPort {
  registerIdentityCleanup(cleanup: () => void): () => void;
}

export interface PendingSendDeps {
  entryId: string;
  client: PendingSendClientPort;
  limits: PendingSendLimitsPort;
  view: PendingSendViewPort;
  identity?: PendingSendIdentityPort;
  /** 消息号生成器（默认共享传输的 `newRequestId`）；同一未完成尝试沿用同一个号。 */
  newMessageId?: () => string;
  /** 附件本地身份生成器（默认共享传输的 `newRequestId`）。 */
  newAttachmentId?: () => string;
  /** 时钟（本地过期判定；默认 `Date.now`）。 */
  now?: () => number;
}

export interface PendingSendActions {
  setText(text: string): void;
  addFiles(files: readonly File[]): void;
  removeAttachment(localId: string): void;
  /** 发送当前输入：存在未完成尝试时沿用同一消息号（绝不换号）。 */
  send(options?: WebchatRequestOptions): Promise<PendingSendSnapshot>;
  /** 重放未完成尝试：同一消息号/幂等键/附件身份（响应未知时的唯一正确动作）。 */
  retry(options?: WebchatRequestOptions): Promise<PendingSendSnapshot>;
  /** 中止在途上传/提交（身份保留，结果按"未知"处理）。 */
  cancel(): void;
  /** 显式放弃未完成尝试（输入保留可继续编辑，下一次发送用新消息号）。 */
  discard(): void;
  reset(): void;
}

export interface PendingSendService extends PendingSendActions {
  snapshot(): PendingSendSnapshot;
  subscribe(listener: (snapshot: PendingSendSnapshot) => void): () => void;
  dispose(): void;
}

/** React 绑定结果：快照是值（`useSyncExternalStore`），动作与服务实例同一份。 */
export interface PendingSendHookResult extends PendingSendActions {
  snapshot: PendingSendSnapshot;
}

interface InputScope {
  conversationId: string | null;
  text: string;
  attachments: ComposerAttachment[];
}

interface DispatchState {
  controller: AbortController;
  reason: 'view' | 'cancel' | null;
  cancel(): void;
  cleanup(): void;
}

interface PendingSendRuntime {
  deps: PendingSendDeps;
  input: InputScope;
  attempt: PendingSendAttempt | null;
  phase: PendingSendPhase;
  errorCode: string | null;
  inputErrorCode: string | null;
  outcomeUnknown: boolean;
  expiredUploadIds: string[];
  accepted: WebchatMessageAccepted | null;
  /** 真实文件句柄：只存在内存里，按本地身份取用（快照不携带字节）。 */
  blobs: Map<string, Blob>;
  /** 状态代号：重置/放弃后 fence 迟到响应，旧尝试不写回新状态。 */
  epoch: number;
  dispatch: DispatchState | null;
  listeners: Set<(snapshot: PendingSendSnapshot) => void>;
  cached: PendingSendSnapshot | null;
  inFlight: Promise<PendingSendSnapshot> | null;
}

/** UTF-8 字节长度：预检口径与后端一致（不拿字符串长度冒充字节数）。 */
export function textByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** 预检：空内容/字节上限/数量上限/单件大小；通过返回 null（服务端仍是最终裁决）。 */
export function precheckSendInput(input: {
  text: string;
  attachments: readonly ComposerAttachment[];
  limits: ComposerAttachmentPolicy;
}): PendingSendErrorCode | null {
  const { text, attachments, limits } = input;
  if (text.trim() === '' && attachments.length === 0) return 'SEND_EMPTY';
  if (textByteLength(text) > limits.text_max_bytes) return 'SEND_TEXT_TOO_LONG';
  if (attachments.length > limits.upload_max_per_message) return 'SEND_TOO_MANY_FILES';
  if (attachments.some((entry) => entry.size > limits.upload_max_bytes)) return 'SEND_FILE_TOO_LARGE';
  return null;
}

/** 已暂存且 `expires_at` 早于参考时刻的上传身份（时间不可解析时不猜：不算过期）。 */
export function expiredStagedUploadIds(
  attachments: readonly ComposerAttachment[],
  nowMs: number
): string[] {
  const ids: string[] = [];
  for (const entry of attachments) {
    if (entry.uploadId === null || entry.expiresAt === null) continue;
    const expiresAt = Date.parse(entry.expiresAt);
    if (Number.isFinite(expiresAt) && expiresAt <= nowMs) ids.push(entry.uploadId);
  }
  return ids;
}

/** 结果未知：没有拿到确定响应（传输失败/中止）——重试必须沿用原消息号。 */
export function isUnknownSendOutcome(error: unknown, aborted: boolean): boolean {
  if (aborted) return true;
  const response = (error as { response?: { status?: number } } | null)?.response;
  return typeof response?.status !== 'number';
}

const PENDING_SEND_ERROR_KEYS: Record<string, string> = {
  SEND_ENTRY_REQUIRED: 'chat.composer.error.entryRequired',
  SEND_CONVERSATION_REQUIRED: 'chat.composer.error.conversationRequired',
  SEND_EMPTY: 'chat.composer.error.empty',
  SEND_TEXT_TOO_LONG: 'chat.composer.error.textTooLong',
  SEND_TOO_MANY_FILES: 'chat.composer.error.tooManyFiles',
  SEND_FILE_TOO_LARGE: 'chat.composer.error.fileTooLarge',
  SEND_VIEW_CHANGED: 'chat.composer.error.viewChanged',
  SEND_ABORTED: 'chat.composer.error.aborted',
  SEND_ATTACHMENT_UPLOAD_FAILED: 'chat.composer.error.uploadFailed',
  CLIENT_CONFIG_NOT_READY: 'chat.composer.error.notReady',
  CLIENT_CONFIG_INVALID: 'chat.composer.error.notReady',
  ATTACHMENT_TYPE_NOT_ALLOWED: 'chat.composer.error.typeNotAllowed',
  ATTACHMENT_TOO_LARGE: 'chat.composer.error.fileTooLarge',
  ATTACHMENT_COUNT_EXCEEDED: 'chat.composer.error.tooManyFiles',
  UPLOAD_EXPIRED: 'chat.composer.error.stagingExpired',
  UPLOAD_NOT_FOUND: 'chat.composer.error.stagingExpired',
  NETWORK_ERROR: 'chat.composer.error.network',
  RUN_CAPACITY_EXCEEDED: 'chat.composer.error.capacity',
  RUN_QUEUE_TIMEOUT: 'chat.composer.error.capacity',
  MODEL_CAPACITY_UNAVAILABLE: 'chat.composer.error.capacity',
  MODEL_WAIT_TIMEOUT: 'chat.composer.error.capacity'
};

/** 稳定码 → 本地化词条键；未登记/未知回落显式失败文案（绝不静默）。 */
export function pendingSendErrorKey(code: string | null): string {
  if (code === null) return 'chat.composer.error.failed';
  return PENDING_SEND_ERROR_KEYS[code] ?? 'chat.composer.error.failed';
}

/** 稳定错误码：本地显式错误原样上抛，其余（服务端/传输）交给模块 service 收敛。 */
function stableErrorCode(error: unknown): string {
  if (error instanceof ClientConfigError) return error.code;
  return webchatErrorCode(error);
}

function createRuntime(deps: PendingSendDeps): PendingSendRuntime {
  return {
    deps,
    input: { conversationId: null, text: '', attachments: [] },
    attempt: null,
    phase: 'IDLE',
    errorCode: null,
    inputErrorCode: null,
    outcomeUnknown: false,
    expiredUploadIds: [],
    accepted: null,
    blobs: new Map(),
    epoch: 0,
    dispatch: null,
    listeners: new Set(),
    cached: null,
    inFlight: null
  };
}

function currentConversationId(runtime: PendingSendRuntime): string | null {
  return runtime.deps.view.conversationId();
}

/** 当前会话可见的尝试；其它会话的尝试被隔离（不跨会话展示/改投）。 */
function visibleAttempt(runtime: PendingSendRuntime): PendingSendAttempt | null {
  const attempt = runtime.attempt;
  if (attempt === null) return null;
  return attempt.conversationId === currentConversationId(runtime) ? attempt : null;
}

/** 输入冻结：已提交（payload 冻结待重放）或在途（不中途改 payload）。 */
function inputFrozen(runtime: PendingSendRuntime, attempt: PendingSendAttempt): boolean {
  if (attempt.submitted) return true;
  return runtime.phase === 'STAGING' || runtime.phase === 'PENDING';
}

/** 可编辑目标：可见尝试优先（未提交时），否则当前会话的输入作用域；冻结时返回 null。 */
function editableTarget(runtime: PendingSendRuntime): InputScope | null {
  const attempt = visibleAttempt(runtime);
  if (attempt !== null) return inputFrozen(runtime, attempt) ? null : attempt;
  return ensureInputScope(runtime, currentConversationId(runtime));
}

/** 输入作用域：按会话隔离（切回仍可续写）；作用域变化时换新槽，不把旧会话输入带过去。 */
function ensureInputScope(runtime: PendingSendRuntime, conversationId: string | null): InputScope {
  if (runtime.input.conversationId !== conversationId) {
    runtime.input = { conversationId, text: '', attachments: [] };
  }
  return runtime.input;
}

function readLimits(runtime: PendingSendRuntime): ComposerAttachmentPolicy | null {
  try {
    return runtime.deps.limits.limits();
  } catch (error) {
    if (error instanceof ClientConfigError) return null;
    throw error;
  }
}

function readNow(runtime: PendingSendRuntime): number {
  return runtime.deps.now === undefined ? Date.now() : runtime.deps.now();
}

function createAttachment(localId: string, file: File): ComposerAttachment {
  return {
    localId,
    filename: file.name,
    size: file.size,
    mediaType: file.type,
    status: 'READY',
    uploadId: null,
    expiresAt: null,
    errorCode: null
  };
}

function cloneAttachment(entry: ComposerAttachment): ComposerAttachment {
  return { ...entry };
}

/** 暂存回执落地：服务端元信息是权威（文件名/大小/类型以服务端为准）。 */
function applyUpload(attachment: ComposerAttachment, uploaded: WebchatUpload): void {
  attachment.status = 'UPLOADED';
  attachment.uploadId = uploaded.upload_id;
  attachment.expiresAt = uploaded.expires_at;
  attachment.errorCode = null;
  if (uploaded.filename !== null && uploaded.filename.trim() !== '') {
    attachment.filename = uploaded.filename;
  }
  if (uploaded.size > 0) attachment.size = uploaded.size;
  if (uploaded.media_type.trim() !== '') attachment.mediaType = uploaded.media_type;
}

function emit(runtime: PendingSendRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象；视图换会话时重算。 */
function currentSnapshot(runtime: PendingSendRuntime): PendingSendSnapshot {
  const conversationId = currentConversationId(runtime);
  const cached = runtime.cached;
  if (cached !== null && cached.conversationId === conversationId) return cached;
  const attempt = visibleAttempt(runtime);
  const scope = runtime.input.conversationId === conversationId ? runtime.input : null;
  // 尝试不可见（别的会话）时，失败面也一并隔离；没有尝试时预检错误仍属于当前输入。
  const visible = attempt !== null || runtime.attempt === null;
  runtime.cached = {
    conversationId,
    phase: attempt === null ? 'IDLE' : runtime.phase,
    attempt,
    text: attempt === null ? scope?.text ?? '' : attempt.text,
    attachments: attempt === null ? scope?.attachments ?? [] : attempt.attachments,
    inputFrozen: attempt !== null && inputFrozen(runtime, attempt),
    inputErrorCode: runtime.inputErrorCode,
    errorCode: visible ? runtime.errorCode : null,
    outcomeUnknown: visible ? runtime.outcomeUnknown : false,
    expiredUploadIds: runtime.expiredUploadIds,
    accepted: runtime.accepted
  };
  return runtime.cached;
}

function setText(runtime: PendingSendRuntime, text: string): void {
  const target = editableTarget(runtime);
  if (target === null) return;
  target.text = text;
  emit(runtime);
}

function addFiles(runtime: PendingSendRuntime, files: readonly File[]): void {
  const target = editableTarget(runtime);
  if (target === null) return;
  const limits = readLimits(runtime);
  if (limits === null) {
    runtime.inputErrorCode = 'CLIENT_CONFIG_NOT_READY';
    emit(runtime);
    return;
  }
  let rejected: PendingSendErrorCode | null = null;
  for (const file of files) {
    if (target.attachments.length >= limits.upload_max_per_message) {
      rejected = 'SEND_TOO_MANY_FILES';
      break;
    }
    if (file.size > limits.upload_max_bytes) {
      rejected = 'SEND_FILE_TOO_LARGE';
      continue;
    }
    const localId = runtime.deps.newAttachmentId === undefined
      ? newRequestId()
      : runtime.deps.newAttachmentId();
    target.attachments.push(createAttachment(localId, file));
    runtime.blobs.set(localId, file);
  }
  runtime.inputErrorCode = rejected;
  emit(runtime);
}

function removeAttachment(runtime: PendingSendRuntime, localId: string): void {
  const target = editableTarget(runtime);
  if (target === null) return;
  const index = target.attachments.findIndex((entry) => entry.localId === localId);
  if (index === -1) return;
  target.attachments.splice(index, 1);
  runtime.blobs.delete(localId);
  emit(runtime);
}

function createAttempt(
  runtime: PendingSendRuntime,
  conversationId: string,
  scope: InputScope
): PendingSendAttempt {
  const messageId = runtime.deps.newMessageId === undefined
    ? newRequestId()
    : runtime.deps.newMessageId();
  return {
    conversationId,
    messageId,
    idempotencyKey: messageId,
    text: scope.text,
    uploadIds: [],
    submitted: false,
    attachments: scope.attachments
  };
}

/** 需要上传的附件：没有暂存身份的一律补传；未提交的尝试还要重传本地判定过期的暂存身份。 */
function pendingUploads(runtime: PendingSendRuntime, attempt: PendingSendAttempt): ComposerAttachment[] {
  return attempt.attachments.filter(
    (entry) =>
      entry.uploadId === null ||
      (!attempt.submitted && runtime.expiredUploadIds.includes(entry.uploadId))
  );
}

type DispatchPlan =
  | { ok: false; errorCode: string }
  | { ok: true; attempt: PendingSendAttempt; pending: ComposerAttachment[] };

/** 规划一次发送：固定/沿用尝试身份，刷新未提交 payload，并算出要补传的附件。 */
function prepareDispatch(runtime: PendingSendRuntime): DispatchPlan {
  if (runtime.deps.entryId.trim() === '') return { ok: false, errorCode: 'SEND_ENTRY_REQUIRED' };
  const conversationId = currentConversationId(runtime);
  if (conversationId === null) return { ok: false, errorCode: 'SEND_CONVERSATION_REQUIRED' };
  const limits = readLimits(runtime);
  if (limits === null) return { ok: false, errorCode: 'CLIENT_CONFIG_NOT_READY' };
  const existing = runtime.attempt;
  if (existing !== null && existing.conversationId !== conversationId) {
    return { ok: false, errorCode: 'SEND_VIEW_CHANGED' };
  }
  if (existing !== null && existing.submitted) {
    refreshExpiry(runtime, existing.attachments);
    return { ok: true, attempt: existing, pending: pendingUploads(runtime, existing) };
  }
  const scope = ensureInputScope(runtime, conversationId);
  const precheck = precheckSendInput({ text: scope.text, attachments: scope.attachments, limits });
  if (precheck !== null) return { ok: false, errorCode: precheck };
  const attempt = existing === null ? createAttempt(runtime, conversationId, scope) : existing;
  attempt.text = scope.text;
  attempt.attachments = scope.attachments;
  runtime.attempt = attempt;
  refreshExpiry(runtime, attempt.attachments);
  return { ok: true, attempt, pending: pendingUploads(runtime, attempt) };
}

function beginDispatch(runtime: PendingSendRuntime, options: WebchatRequestOptions): DispatchState {
  const controller = new AbortController();
  const state: DispatchState = {
    controller,
    reason: null,
    cancel: () => {},
    cleanup: () => {}
  };
  const abort = (reason: 'view' | 'cancel'): void => {
    if (state.reason === null) state.reason = reason;
    controller.abort();
  };
  const viewSignal = runtime.deps.view.viewSignal();
  const onView = (): void => abort('view');
  const callerSignal = options.signal;
  const onCaller = (): void => abort('cancel');
  if (viewSignal.aborted) onView();
  else viewSignal.addEventListener('abort', onView, { once: true });
  if (callerSignal !== undefined) {
    if (callerSignal.aborted) onCaller();
    else callerSignal.addEventListener('abort', onCaller, { once: true });
  }
  state.cancel = () => abort('cancel');
  state.cleanup = () => {
    viewSignal.removeEventListener('abort', onView);
    if (callerSignal !== undefined) callerSignal.removeEventListener('abort', onCaller);
  };
  runtime.dispatch = state;
  return state;
}

function endDispatch(runtime: PendingSendRuntime, state: DispatchState): void {
  state.cleanup();
  if (runtime.dispatch === state) runtime.dispatch = null;
}

/** 中止/失败时的稳定码：视图变化与用户取消分开，其余按服务端目录码。 */
function dispatchFailureCode(error: unknown, state: DispatchState): string {
  if (!state.controller.signal.aborted) return stableErrorCode(error);
  return state.reason === 'cancel' ? 'SEND_ABORTED' : 'SEND_VIEW_CHANGED';
}

async function uploadAttachment(
  runtime: PendingSendRuntime,
  attachment: ComposerAttachment,
  state: DispatchState
): Promise<string | null> {
  const blob = runtime.blobs.get(attachment.localId);
  if (blob === undefined) {
    attachment.status = 'FAILED';
    attachment.errorCode = 'SEND_ATTACHMENT_UPLOAD_FAILED';
    emit(runtime);
    return 'SEND_ATTACHMENT_UPLOAD_FAILED';
  }
  attachment.status = 'UPLOADING';
  attachment.errorCode = null;
  emit(runtime);
  try {
    const uploaded = await runtime.deps.client.upload(runtime.deps.entryId, blob, {
      signal: state.controller.signal
    });
    applyUpload(attachment, uploaded);
    emit(runtime);
    return null;
  } catch (error) {
    const code = dispatchFailureCode(error, state);
    attachment.status = 'FAILED';
    attachment.uploadId = null;
    attachment.expiresAt = null;
    attachment.errorCode = code;
    emit(runtime);
    return code === 'UNKNOWN' ? 'SEND_ATTACHMENT_UPLOAD_FAILED' : code;
  }
}

async function stageAttachments(
  runtime: PendingSendRuntime,
  pending: readonly ComposerAttachment[],
  state: DispatchState
): Promise<string | null> {
  for (const attachment of pending) {
    const code = await uploadAttachment(runtime, attachment, state);
    if (code !== null) return code;
  }
  return null;
}

/** 本地过期判定刷新：在下一次动作/失败落地时重算（不用定时器；服务端仍是权威）。 */
function refreshExpiry(runtime: PendingSendRuntime, attachments: readonly ComposerAttachment[]): void {
  runtime.expiredUploadIds = expiredStagedUploadIds(attachments, readNow(runtime));
}

/** 202 落地：清空输入与尝试，只保留服务端回执（不显示伪成功）。 */
function applyAccepted(
  runtime: PendingSendRuntime,
  attempt: PendingSendAttempt,
  accepted: WebchatMessageAccepted
): void {
  runtime.attempt = null;
  runtime.phase = 'IDLE';
  runtime.errorCode = null;
  runtime.outcomeUnknown = false;
  runtime.inputErrorCode = null;
  runtime.expiredUploadIds = [];
  runtime.accepted = accepted;
  for (const entry of attempt.attachments) runtime.blobs.delete(entry.localId);
  runtime.input = { conversationId: attempt.conversationId, text: '', attachments: [] };
  emit(runtime);
}

/** 提交失败：保留原身份与输入（结果未知时只重放，不换号、不改投）。 */
function applySubmitFailure(
  runtime: PendingSendRuntime,
  attempt: PendingSendAttempt,
  error: unknown,
  state: DispatchState
): void {
  const aborted = state.controller.signal.aborted;
  runtime.attempt = attempt;
  runtime.phase = 'FAILED';
  runtime.errorCode = dispatchFailureCode(error, state);
  runtime.outcomeUnknown = isUnknownSendOutcome(error, aborted);
  refreshExpiry(runtime, attempt.attachments);
  emit(runtime);
}

async function submitAttempt(
  runtime: PendingSendRuntime,
  attempt: PendingSendAttempt,
  state: DispatchState,
  epoch: number
): Promise<PendingSendSnapshot> {
  attempt.submitted = true;
  attempt.attachments = attempt.attachments.map(cloneAttachment);
  runtime.phase = 'PENDING';
  runtime.accepted = null;
  emit(runtime);
  const input: WebchatSubmitInput = {
    conversation_id: attempt.conversationId,
    message_id: attempt.messageId,
    text: attempt.text,
    upload_ids: [...attempt.uploadIds]
  };
  const write: WebchatWriteOptions = {
    idempotencyKey: attempt.messageId,
    signal: state.controller.signal
  };
  try {
    const accepted = await runtime.deps.client.submit(runtime.deps.entryId, input, write);
    if (runtime.epoch === epoch) applyAccepted(runtime, attempt, accepted);
  } catch (error) {
    if (runtime.epoch === epoch) applySubmitFailure(runtime, attempt, error, state);
  }
  return currentSnapshot(runtime);
}

async function runAttempt(
  runtime: PendingSendRuntime,
  attempt: PendingSendAttempt,
  pending: readonly ComposerAttachment[],
  options: WebchatRequestOptions
): Promise<PendingSendSnapshot> {
  const epoch = runtime.epoch;
  const state = beginDispatch(runtime, options);
  try {
    runtime.phase = 'STAGING';
    runtime.errorCode = null;
    runtime.outcomeUnknown = false;
    emit(runtime);
    const failed = await stageAttachments(runtime, pending, state);
    if (runtime.epoch !== epoch) return currentSnapshot(runtime);
    refreshExpiry(runtime, attempt.attachments);
    if (failed !== null) {
      runtime.phase = 'FAILED';
      runtime.errorCode = failed;
      runtime.outcomeUnknown = false;
      emit(runtime);
      return currentSnapshot(runtime);
    }
    attempt.uploadIds = attempt.attachments
      .map((entry) => entry.uploadId)
      .filter((id): id is string => id !== null);
    return await submitAttempt(runtime, attempt, state, epoch);
  } finally {
    endDispatch(runtime, state);
  }
}

/** 发送与重试共用同一入口：存在未完成尝试就沿用同一消息号（按钮怎么点都不换号）。 */
function dispatch(
  runtime: PendingSendRuntime,
  options: WebchatRequestOptions
): Promise<PendingSendSnapshot> {
  if (runtime.inFlight !== null) return runtime.inFlight;
  const plan = prepareDispatch(runtime);
  if (!plan.ok) {
    // 规划失败属于输入侧（预检/状态）：只落输入错误面，不伪造一次失败的发送尝试。
    runtime.inputErrorCode = plan.errorCode;
    emit(runtime);
    return Promise.resolve(currentSnapshot(runtime));
  }
  const action = runAttempt(runtime, plan.attempt, plan.pending, options);
  const settled = action.finally(() => {
    if (runtime.inFlight === settled) runtime.inFlight = null;
  });
  runtime.inFlight = settled;
  return settled;
}

/** 显式放弃尝试：输入保留（可继续编辑），下一次发送用新消息号。 */
function discard(runtime: PendingSendRuntime): void {
  runtime.epoch += 1;
  runtime.dispatch?.cancel();
  const attempt = runtime.attempt;
  if (attempt !== null) {
    const scope = ensureInputScope(runtime, attempt.conversationId);
    scope.text = attempt.text;
    scope.attachments = attempt.attachments;
  }
  runtime.attempt = null;
  runtime.phase = 'IDLE';
  runtime.errorCode = null;
  runtime.outcomeUnknown = false;
  runtime.expiredUploadIds = [];
  emit(runtime);
}

function reset(runtime: PendingSendRuntime): void {
  runtime.epoch += 1;
  runtime.dispatch?.cancel();
  runtime.input = { conversationId: null, text: '', attachments: [] };
  runtime.attempt = null;
  runtime.phase = 'IDLE';
  runtime.errorCode = null;
  runtime.inputErrorCode = null;
  runtime.outcomeUnknown = false;
  runtime.expiredUploadIds = [];
  runtime.accepted = null;
  runtime.blobs.clear();
  emit(runtime);
}

/** 构造页面级输入/发送状态控制器；一个页面一个实例（React 绑定见 `usePendingSend`）。 */
export function createPendingSend(deps: PendingSendDeps): PendingSendService {
  const runtime = createRuntime(deps);
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    setText: (text) => setText(runtime, text),
    addFiles: (files) => addFiles(runtime, files),
    removeAttachment: (localId) => removeAttachment(runtime, localId),
    send: (options = {}) => dispatch(runtime, options),
    retry: (options = {}) => dispatch(runtime, options),
    cancel() {
      runtime.dispatch?.cancel();
    },
    discard: () => discard(runtime),
    reset: () => reset(runtime),
    dispose() {
      runtime.listeners.clear();
      reset(runtime);
    }
  };
}

/**
 * 绑定输入/发送状态：状态服务在页面生命周期内稳定（依赖必须是稳定引用），快照经
 * `useSyncExternalStore` 订阅；身份边界（退出/切主体）清空尝试与输入，卸载时释放订阅。
 */
export function usePendingSend(deps: PendingSendDeps): PendingSendHookResult {
  // 依赖语义与 `useConversations` 一致：调用方提供稳定引用（client/limits/view/identity）。
  const service = useMemo(() => createPendingSend(deps), [deps]);
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);
  const registerIdentityCleanup = deps.identity?.registerIdentityCleanup;

  useEffect(() => {
    if (registerIdentityCleanup === undefined) return undefined;
    return registerIdentityCleanup(() => {
      service.reset();
    });
  }, [registerIdentityCleanup, service]);

  useEffect(() => () => service.dispose(), [service]);

  const actions = useMemo<PendingSendActions>(
    () => ({
      setText: (text: string) => service.setText(text),
      addFiles: (files: readonly File[]) => service.addFiles(files),
      removeAttachment: (localId: string) => service.removeAttachment(localId),
      send: (options?: WebchatRequestOptions) => service.send(options),
      retry: (options?: WebchatRequestOptions) => service.retry(options),
      cancel: () => service.cancel(),
      discard: () => service.discard(),
      reset: () => service.reset()
    }),
    [service]
  );

  return { snapshot, ...actions };
}
