/**
 * 后台会话活动、未读与安全附件预览（design frontend §3.3 `NotificationPanel`、§3.5 `files/notifications`；
 * TASK-084）。
 *
 * **有界增量轮询**：只按 API-11 的用户 feed 做增量读取——`after_seq` 取上一次响应的 `next_seq`，
 * 未追平 `through_seq` 时立即续拉、追平后按 API-19 投影的 `webchat.notification_poll_sec` 排下一轮；
 * 轮询间隔加固定比例抖动（`NOTIFICATION_POLL_JITTER_RATIO`）；页大小只取投影的
 * `webchat.history_page_size`（≤ 后端分页上限），随每次增量请求发送；非法投影值显式失败，
 * 不回落猜测默认值、也不复制任何后端默认值。
 *
 * **隐藏页暂停/前台同步**：页面隐藏时清掉待执行定时器并停在暂停态（零请求）；回到前台立即增量同步，
 * 并经 `activity.foreground()` 让页面同步会话索引（会话状态属 TASK-078 控制器，这里只发信号）。
 *
 * **未读以服务端为准**：未读总数只取服务端 `unread_count`（缺省/非法 → 0），绝不按重放帧累加、
 * 绝不本地自减；按会话未读只由服务端通知行的 `read_at === null` 聚合，可直接喂 TASK-079 的侧栏接缝；
 * `read` 只由显式 `markRead()` 触发（同步/前台同步/定位都零写请求），已读回执落 `read_at` 后立即以
 * 服务端权威结果重读未读总数。
 *
 * **安全附件预览**：预览只经带认证上下文的 `WebchatClient.download` 取字节（`responseType: 'blob'`），
 * 只对白名单栅格图片（`kind=IMAGE` ∧ `available` ∧ `image/png|jpeg|gif|webp`，与 TASK-055 的服务端
 * `inline` 白名单同口径）创建 Blob URL；失效附件与其它类型零请求且显式失败。Blob URL 在关闭/替换
 * 预览时立即撤销，并登记到身份作用域（`trackObjectUrl`），身份边界统一撤销；本模块不落任何持久缓存、
 * 不拼装制品路径、不为附件生成 href。
 *
 * 身份边界（退出/到期/撤销/冲突/切主体）经认证状态机的 `registerIdentityCleanup` 触发 `reset()`：
 * 停轮询、清条目/游标/未读/预览并撤销 Blob URL，不把上一个主体的通知留给下一个主体。认证失效
 * （CHAT 401）只留稳定码并停止轮询，绝不自动重试/自动补拉。
 *
 * 本文件不发 HTTP、不拼装传输头、不读 Cookie、不打日志、不持有文案键。
 */

import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';

import type {
  NotificationStreamHandle,
  NotificationStreamPort
} from '../services/notificationStream';
import { isWebchatAuthError, webchatErrorCode } from '../services/webchatApi';
import type {
  WebchatAttachment,
  WebchatClient,
  WebchatClientLimits,
  WebchatNotification,
  WebchatNotificationPage,
  WebchatNotificationQuery,
  WebchatNotificationReadReceipt,
  WebchatRequestOptions
} from '../types';
import type { ChatAuthController } from './useChatAuth';
import type { ClientConfigController } from './useClientConfig';

/** 轮询间隔的固定比例抖动（design backend §3.7「实际轮询加固定比例抖动」）：±20%。 */
export const NOTIFICATION_POLL_JITTER_RATIO = 0.2;

/** 后端分页上限（`muad_contracts.MAX_PAGE_SIZE`）：投影页大小不得超过它。 */
export const NOTIFICATION_MAX_PAGE_SIZE = 100;

/** 可内联预览的媒体类型白名单（栅格图片）：与 TASK-055 服务端 `inline` 白名单同口径。 */
export const PREVIEWABLE_MEDIA_TYPES: readonly string[] = [
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp'
];

/** 面板/通知的加载态：首次成功前不展示列表，失败保留已有结果。 */
export type NotificationLoadState = 'idle' | 'loading' | 'ready' | 'failed';

/** 本地边界失败码（不是服务端目录码，也不回落猜测默认）。 */
export type NotificationErrorCode =
  | 'NOTIFICATIONS_NOT_READY'
  | 'POLL_INTERVAL_INVALID'
  | 'PAGE_SIZE_INVALID'
  | 'NOTIFICATION_REQUIRED'
  | 'NOTIFICATION_UNKNOWN'
  | 'ATTACHMENT_NOT_PREVIEWABLE';

/** 本地前置校验失败：稳定码 + 无 HTTP 副作用。 */
export class NotificationStateError extends Error {
  readonly code: NotificationErrorCode;

  constructor(code: NotificationErrorCode) {
    super(code);
    this.name = 'NotificationStateError';
    this.code = code;
  }
}

/** 抖动后的轮询间隔：`pollSec` 只来自 API-19 投影；非法值显式失败，不回落猜测默认。 */
export function notificationPollDelayMs(pollSec: number, random: number): number {
  if (typeof pollSec !== 'number' || !Number.isFinite(pollSec) || pollSec <= 0) {
    throw new NotificationStateError('POLL_INTERVAL_INVALID');
  }
  const unit =
    typeof random === 'number' && Number.isFinite(random) ? Math.min(Math.max(random, 0), 1) : 0;
  const factor = 1 - NOTIFICATION_POLL_JITTER_RATIO + 2 * NOTIFICATION_POLL_JITTER_RATIO * unit;
  return Math.round(pollSec * 1000 * factor);
}

/** 通知页大小：只取 API-19 投影且不超过后端分页上限；非法值显式失败，不回落猜测默认。 */
export function notificationPageSize(limits: WebchatClientLimits): number {
  const value = limits.history_page_size;
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < 1 ||
    value > NOTIFICATION_MAX_PAGE_SIZE
  ) {
    throw new NotificationStateError('PAGE_SIZE_INVALID');
  }
  return value;
}

/** 未读徽标：只吃服务端权威计数；缺省/非法/非正数一律 0（不显示徽标，不伪造未读）。 */export function unreadBadgeCount(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/**
 * 合并增量条目：按 `id` 去重、同一 id 用最新服务端事实覆盖、按 `seq` 升序。
 * 有界集合先丢最旧的**已读**条目（未读事实不因展示上界丢失），仍超界才丢最旧未读。
 */
export function mergeNotificationItems(
  existing: readonly WebchatNotification[],
  incoming: readonly WebchatNotification[],
  maxItems: number
): WebchatNotification[] {
  const byId = new Map<string, WebchatNotification>();
  for (const item of [...existing, ...incoming]) {
    const previous = byId.get(item.id);
    byId.set(item.id, previous === undefined ? item : { ...previous, ...item });
  }
  const merged = [...byId.values()].sort((left, right) => left.seq - right.seq);
  if (maxItems <= 0 || merged.length <= maxItems) return merged;
  const dropOrder = [...merged].sort(
    (left, right) =>
      Number(left.read_at === null) - Number(right.read_at === null) || left.seq - right.seq
  );
  const dropped = new Set(dropOrder.slice(0, merged.length - maxItems).map((item) => item.id));
  return merged.filter((item) => !dropped.has(item.id));
}

/** 按会话未读：只由服务端通知行的 `read_at === null` 聚合（不按重放帧累加、不本地自增）。 */
export function unreadByConversation(
  items: readonly WebchatNotification[]
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) {
    if (item.read_at !== null) continue;
    counts[item.conversation_id] = (counts[item.conversation_id] ?? 0) + 1;
  }
  return counts;
}

/** 展示顺序：按服务端 `seq` 倒序（最新在前），不按时间重排、不按下标决定。 */
export function notificationDisplayOrder(
  items: readonly WebchatNotification[]
): WebchatNotification[] {
  return [...items].sort((left, right) => right.seq - left.seq);
}

/** 预览资格：可用 ∧ `IMAGE` ∧ 白名单栅格图片；失效附件与其它类型一律不预览。 */
export function canPreviewAttachment(attachment: WebchatAttachment): boolean {
  return (
    attachment.available === true &&
    attachment.kind === 'IMAGE' &&
    PREVIEWABLE_MEDIA_TYPES.includes(attachment.media_type)
  );
}

/** 通知端口：只取 API-11 与授权下载面的三个方法（结构上没有任何写消息/认证能力）。 */
export interface NotificationClientPort {
  notifications(
    query?: WebchatNotificationQuery,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationPage>;
  read(
    notificationId: string,
    options?: WebchatRequestOptions
  ): Promise<WebchatNotificationReadReceipt>;
  download(artifactId: string, options?: WebchatRequestOptions): Promise<Blob>;
}

/** 轮询配置端口：`ClientConfigService`/`ClientConfigController` 结构满足（间隔只来自投影）。 */
export interface NotificationPollingConfigPort {
  ready(): boolean;
  limits(): WebchatClientLimits;
}

/** 身份端口：`ChatAuthenticationService`/`ChatAuthController` 结构满足。 */
export interface NotificationsIdentityPort {
  registerIdentityCleanup(cleanup: () => void): () => void;
  trackObjectUrl(url: string): void;
  identitySignal(): AbortSignal;
}

/** 定时器端口：可注入以便确定性驱动与统一取消（不散落 `setInterval`）。 */
export interface NotificationTimers {
  setTimeout(handler: () => void, delayMs: number): number;
  clearTimeout(handle: number): void;
}

/** 页面可见性端口：隐藏页暂停常规轮询，回前台立即同步。 */
export interface NotificationVisibilityPort {
  hidden(): boolean;
  subscribe(listener: () => void): () => void;
}

/** Blob URL 端口：创建/撤销预览 URL（默认 `URL.createObjectURL`/`URL.revokeObjectURL`）。 */
export interface NotificationObjectUrlPort {
  createObjectUrl(blob: Blob): string;
  revokeObjectUrl(url: string): void;
}

/** 回前台同步端口：通知由本控制器同步，会话索引由页面端口刷新（TASK-078 控制器）。 */
export interface NotificationActivityPort {
  foreground(): void;
}

/** 通知流端口由 `services/notificationStream.ts` 定义；这里只做类型透传，避免两处形状漂移。 */
export type {
  NotificationStreamHandle,
  NotificationStreamHandlers,
  NotificationStreamPort
} from '../services/notificationStream';

/** 当前预览：只持有授权下载面创建的 Blob URL。 */
export interface NotificationPreviewState {
  artifactId: string;
  url: string;
}

export interface NotificationSnapshot {
  loadState: NotificationLoadState;
  items: readonly WebchatNotification[];
  /** 服务端权威未读总数（API-11 `unread_count`）。 */
  unreadCount: number;
  /** 按会话未读（服务端 `read_at === null` 聚合）：直接喂 TASK-079 的侧栏接缝。 */
  unreadByConversation: Readonly<Record<string, number>>;
  /** 增量游标（服务端 `next_seq`）：下一次增量读取的起点。 */
  cursor: number;
  /** 最近一次同步/标读的稳定目录码；成功为 null。 */
  errorCode: string | null;
  /** 常规轮询是否在运行（隐藏页/认证失效/未启动时为 false）。 */
  polling: boolean;
  preview: NotificationPreviewState | null;
}

export interface NotificationController {
  snapshot(): NotificationSnapshot;
  subscribe(listener: () => void): () => void;
  start(): void;
  stop(): void;
  /** 立即增量同步一次（前台同步/显式重试）；失败只留稳定码，不抛出。 */
  sync(options?: WebchatRequestOptions): Promise<NotificationSnapshot>;
  retry(options?: WebchatRequestOptions): Promise<NotificationSnapshot>;
  /** 显式标记单条已读（唯一写入口）；本地前置失败显式抛出且零请求。 */
  markRead(
    notificationId: string,
    options?: WebchatRequestOptions
  ): Promise<NotificationSnapshot>;
  /** 经授权下载面预览白名单图片；本地拒绝显式抛出，请求失败返回 null 并留稳定码。 */
  openPreview(
    attachment: WebchatAttachment,
    options?: WebchatRequestOptions
  ): Promise<NotificationPreviewState | null>;
  closePreview(): void;
  /** 身份边界清场：停轮询、清条目/游标/未读/预览并撤销 Blob URL。 */
  reset(): void;
  dispose(): void;
}

export interface NotificationControllerDeps {
  client: NotificationClientPort;
  config: NotificationPollingConfigPort;
  identity: NotificationsIdentityPort;
  /** 展示缓存上界（页面持有，不是后端投影字段）。 */
  maxItems: number;
  timers?: NotificationTimers;
  visibility?: NotificationVisibilityPort;
  preview?: NotificationObjectUrlPort;
  activity?: NotificationActivityPort;
  stream?: NotificationStreamPort;
  /** 本地动作失败上报端口（绝不静默）；失败端口自身抛错不影响状态机。 */
  onFailure?: (error: unknown) => void;
  random?: () => number;
}

interface NotificationCore {
  loadState: NotificationLoadState;
  items: WebchatNotification[];
  unreadCount: number;
  cursor: number;
  errorCode: string | null;
  polling: boolean;
  preview: NotificationPreviewState | null;
}

interface NotificationRuntime {
  deps: NotificationControllerDeps;
  timers: NotificationTimers;
  visibility: NotificationVisibilityPort;
  preview: NotificationObjectUrlPort;
  random: () => number;
  core: NotificationCore;
  listeners: Set<() => void>;
  cached: NotificationSnapshot | null;
  inFlight: Promise<void> | null;
  timer: number | null;
  started: boolean;
  caughtUp: boolean;
  previewEpoch: number;
  urls: Set<string>;
  reading: Set<string>;
  visibilityOff: (() => void) | null;
  streamHandle: NotificationStreamHandle | null;
}

/** 默认定时器端口（惰性取值：模块加载不触碰浏览器全局）。 */
function defaultTimers(): NotificationTimers {
  return {
    setTimeout: (handler, delayMs) => globalThis.setTimeout(handler, delayMs),
    clearTimeout: (handle) => {
      globalThis.clearTimeout(handle);
    }
  };
}

/** 默认可见性端口：`document.visibilityState` + `visibilitychange`（宿主可注入替代实现）。 */
function defaultVisibility(): NotificationVisibilityPort {
  return {
    hidden: () => typeof document !== 'undefined' && document.visibilityState === 'hidden',
    subscribe: (listener) => {
      if (typeof document === 'undefined') return () => {};
      document.addEventListener('visibilitychange', listener);
      return () => {
        document.removeEventListener('visibilitychange', listener);
      };
    }
  };
}

/** 默认 Blob URL 端口（惰性取值）。 */
function defaultObjectUrls(): NotificationObjectUrlPort {
  return {
    createObjectUrl: (blob) => URL.createObjectURL(blob),
    revokeObjectUrl: (url) => {
      URL.revokeObjectURL(url);
    }
  };
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象。 */
function currentSnapshot(runtime: NotificationRuntime): NotificationSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const { core } = runtime;
  runtime.cached = {
    loadState: core.loadState,
    items: [...core.items],
    unreadCount: core.unreadCount,
    unreadByConversation: unreadByConversation(core.items),
    cursor: core.cursor,
    errorCode: core.errorCode,
    polling: core.polling,
    preview: core.preview
  };
  return runtime.cached;
}

function emit(runtime: NotificationRuntime): void {
  runtime.cached = null;
  currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener();
}

function reportFailure(runtime: NotificationRuntime, error: unknown): void {
  runtime.deps.onFailure?.(error);
}

function setPolling(runtime: NotificationRuntime, value: boolean): void {
  if (runtime.core.polling === value) return;
  runtime.core.polling = value;
  emit(runtime);
}

/** 清掉待执行定时器（暂停/停轮询/立即同步前调用）。 */
function clearTimer(runtime: NotificationRuntime): void {
  if (runtime.timer === null) return;
  runtime.timers.clearTimeout(runtime.timer);
  runtime.timer = null;
}

/** 撤销并清空所有本身份创建的预览 Blob URL（关闭/替换/身份边界/卸载共用）。 */
function releaseUrls(runtime: NotificationRuntime): void {
  runtime.core.preview = null;
  const urls = [...runtime.urls];
  runtime.urls.clear();
  for (const url of urls) runtime.preview.revokeObjectUrl(url);
}

/** 应用增量页：条目按 id 合并、游标推进到 `next_seq`、未读只取服务端权威计数。 */
function applyPage(runtime: NotificationRuntime, page: WebchatNotificationPage): void {
  const { core } = runtime;
  core.items = mergeNotificationItems(core.items, page.items, runtime.deps.maxItems);
  core.cursor = page.next_seq;
  core.unreadCount = unreadBadgeCount(page.unread_count);
  core.loadState = 'ready';
  core.errorCode = null;
  runtime.caughtUp = page.next_seq >= page.through_seq;
  emit(runtime);
}

/** 失败：留稳定码；首次加载失败进 failed，已有结果保留（面板内就地重试）。 */
function applyFailure(runtime: NotificationRuntime, error: unknown): void {
  const { core } = runtime;
  core.errorCode =
    error instanceof NotificationStateError ? error.code : webchatErrorCode(error);
  if (core.loadState !== 'ready') core.loadState = 'failed';
  if (isWebchatAuthError(error)) stopLoop(runtime);
  emit(runtime);
}

function stopLoop(runtime: NotificationRuntime): void {
  runtime.started = false;
  clearTimer(runtime);
  if (runtime.streamHandle !== null) {
    runtime.streamHandle.close();
    runtime.streamHandle = null;
  }
  if (runtime.visibilityOff !== null) {
    runtime.visibilityOff();
    runtime.visibilityOff = null;
  }
  setPolling(runtime, false);
}

async function fetchPage(
  runtime: NotificationRuntime,
  options?: WebchatRequestOptions
): Promise<void> {
  try {
    const page = await runtime.deps.client.notifications(
      {
        page: 1,
        page_size: notificationPageSize(runtime.deps.config.limits()),
        after_seq: runtime.core.cursor
      },
      { signal: options?.signal ?? runtime.deps.identity.identitySignal() }
    );
    applyPage(runtime, page);
  } catch (error) {
    applyFailure(runtime, error);
  }
}

/** 一次增量同步（single-flight）：投影未就绪零请求并留稳定码。 */
async function syncOnce(
  runtime: NotificationRuntime,
  options?: WebchatRequestOptions
): Promise<NotificationSnapshot> {
  if (!runtime.deps.config.ready()) {
    runtime.core.errorCode = 'NOTIFICATIONS_NOT_READY';
    emit(runtime);
    return currentSnapshot(runtime);
  }
  if (runtime.inFlight !== null) {
    await runtime.inFlight;
    return currentSnapshot(runtime);
  }
  if (runtime.core.loadState === 'idle') {
    runtime.core.loadState = 'loading';
    emit(runtime);
  }
  runtime.inFlight = fetchPage(runtime, options);
  try {
    await runtime.inFlight;
  } finally {
    runtime.inFlight = null;
  }
  return currentSnapshot(runtime);
}

/** 排下一轮：未追平水位立即续拉；追平后按投影的抖动间隔。 */
function scheduleNext(runtime: NotificationRuntime): void {
  if (!runtime.started || !runtime.deps.config.ready()) return;
  // 失败后按投影间隔重试，不能因为「未追平」立即空转；成功且未追平时才立即续拉。
  const delay =
    runtime.core.errorCode === null && !runtime.caughtUp
      ? 0
      : notificationPollDelayMs(runtime.deps.config.limits().notification_poll_sec, runtime.random());
  runtime.timer = runtime.timers.setTimeout(() => {
    runtime.timer = null;
    void tick(runtime);
  }, delay);
}

async function tick(runtime: NotificationRuntime): Promise<void> {
  if (!runtime.started) return;
  if (runtime.visibility.hidden()) {
    setPolling(runtime, false);
    return;
  }
  setPolling(runtime, true);
  await syncOnce(runtime);
  scheduleNext(runtime);
}

/** 立即同步一次并重排定时器（前台同步/显式重试/启动共用）。 */
function kick(runtime: NotificationRuntime): void {
  clearTimer(runtime);
  void tick(runtime);
}

/** 回前台同步会话状态：页面端口失败只上报，不影响通知同步本身。 */
function notifyForeground(runtime: NotificationRuntime): void {
  try {
    runtime.deps.activity?.foreground();
  } catch (error) {
    reportFailure(runtime, error);
  }
}

/** 可见性变化：隐藏即清定时器停在暂停态；回前台立即同步通知与会话状态。 */
function onVisibilityChange(runtime: NotificationRuntime): void {
  if (!runtime.started) return;
  if (runtime.visibility.hidden()) {
    clearTimer(runtime);
    if (runtime.streamHandle !== null) {
      runtime.streamHandle.close();
      runtime.streamHandle = null;
    }
    setPolling(runtime, false);
    return;
  }
  notifyForeground(runtime);
  if (runtime.deps.stream !== undefined) void startWithStream(runtime);
  else kick(runtime);
}

function requireNotificationId(notificationId: string): string {
  const trimmed = notificationId?.trim() ?? '';
  if (trimmed === '') throw new NotificationStateError('NOTIFICATION_REQUIRED');
  return trimmed;
}

function throwLocal(runtime: NotificationRuntime, code: NotificationErrorCode): never {
  const error = new NotificationStateError(code);
  reportFailure(runtime, error);
  throw error;
}

/** 已读回执落库事实：只改这一条的 `read_at`，未读总数仍由服务端重读结果决定。 */
function applyReadReceipt(
  runtime: NotificationRuntime,
  receipt: WebchatNotificationReadReceipt
): void {
  runtime.core.items = runtime.core.items.map((item) =>
    item.id === receipt.id ? { ...item, read_at: receipt.read_at } : item
  );
}

async function markReadOnce(
  runtime: NotificationRuntime,
  notificationId: string,
  options?: WebchatRequestOptions
): Promise<NotificationSnapshot> {
  const id = requireNotificationId(notificationId);
  const item = runtime.core.items.find((entry) => entry.id === id);
  if (item === undefined) throwLocal(runtime, 'NOTIFICATION_UNKNOWN');
  if (item.read_at !== null || runtime.reading.has(id)) return currentSnapshot(runtime);
  runtime.reading.add(id);
  try {
    const receipt = await runtime.deps.client.read(id, {
      signal: options?.signal ?? runtime.deps.identity.identitySignal()
    });
    applyReadReceipt(runtime, receipt);
    await syncOnce(runtime, options);
  } catch (error) {
    applyFailure(runtime, error);
    reportFailure(runtime, error);
  } finally {
    runtime.reading.delete(id);
  }
  return currentSnapshot(runtime);
}

async function openPreviewOnce(
  runtime: NotificationRuntime,
  attachment: WebchatAttachment,
  options?: WebchatRequestOptions
): Promise<NotificationPreviewState | null> {
  if (!canPreviewAttachment(attachment)) throwLocal(runtime, 'ATTACHMENT_NOT_PREVIEWABLE');
  const epoch = (runtime.previewEpoch += 1);
  try {
    const blob = await runtime.deps.client.download(attachment.artifact_id, {
      signal: options?.signal ?? runtime.deps.identity.identitySignal()
    });
    const url = runtime.preview.createObjectUrl(blob);
    if (epoch !== runtime.previewEpoch) {
      runtime.preview.revokeObjectUrl(url);
      return null;
    }
    runtime.deps.identity.trackObjectUrl(url);
    releaseUrls(runtime);
    runtime.core.preview = { artifactId: attachment.artifact_id, url };
    runtime.urls.add(url);
    emit(runtime);
    return runtime.core.preview;
  } catch (error) {
    applyFailure(runtime, error);
    reportFailure(runtime, error);
    return null;
  }
}

function resetRuntime(runtime: NotificationRuntime): void {
  stopLoop(runtime);
  runtime.previewEpoch += 1;
  releaseUrls(runtime);
  runtime.reading.clear();
  runtime.core.items = [];
  runtime.core.cursor = 0;
  runtime.core.unreadCount = 0;
  runtime.core.loadState = 'idle';
  runtime.core.errorCode = null;
  runtime.caughtUp = false;
  emit(runtime);
}

/** 构造运行时（含注入端口与全部内部状态）。 */
function createRuntime(deps: NotificationControllerDeps): NotificationRuntime {
  return {
    deps,
    timers: deps.timers ?? defaultTimers(),
    visibility: deps.visibility ?? defaultVisibility(),
    preview: deps.preview ?? defaultObjectUrls(),
    random: deps.random ?? (() => Math.random()),
    core: {
      loadState: 'idle',
      items: [],
      unreadCount: 0,
      cursor: 0,
      errorCode: null,
      polling: false,
      preview: null
    },
    listeners: new Set(),
    cached: null,
    inFlight: null,
    timer: null,
    started: false,
    caughtUp: false,
    previewEpoch: 0,
    urls: new Set(),
    reading: new Set(),
    visibilityOff: null,
    streamHandle: null
  };
}

/** 打开 SSE 通知流：完整 DTO + 服务端 unread_count 直接并入；出错回退到有界轮询。 */
function openStream(runtime: NotificationRuntime): void {
  const stream = runtime.deps.stream;
  if (stream === undefined) return;
  if (runtime.streamHandle !== null) runtime.streamHandle.close();
  clearTimer(runtime);
  // SSE 建流后常规轮询停跑；断流时 onError 才恢复有界轮询。
  setPolling(runtime, false);
  runtime.streamHandle = stream.open(
    runtime.core.cursor,
    {
      onOpen: () => {
        runtime.core.loadState = 'ready';
        runtime.core.errorCode = null;
        runtime.caughtUp = true;
        setPolling(runtime, false);
        emit(runtime);
      },
      onNotification: (notification, unreadCount) => {
        runtime.core.items = mergeNotificationItems(
          runtime.core.items,
          [notification],
          runtime.deps.maxItems
        );
        runtime.core.cursor = Math.max(runtime.core.cursor, notification.seq);
        runtime.core.unreadCount = unreadBadgeCount(unreadCount);
        runtime.core.loadState = 'ready';
        runtime.core.errorCode = null;
        runtime.caughtUp = true;
        setPolling(runtime, false);
        emit(runtime);
      },
      onError: () => {
        if (runtime.streamHandle !== null) {
          runtime.streamHandle.close();
          runtime.streamHandle = null;
        }
        setPolling(runtime, false);
        // 流断开：退回有界轮询，保证不静默丢通知；下次 start/回前台再尝试 SSE。
        kick(runtime);
      }
    },
    { snapshotReady: runtime.caughtUp }
  );
}


/**
 * SSE 启动：先取一次 REST 权威快照（条目/未读/游标），再开 live 流。
 *
 * 这样 gateway 才能在 `after_seq >= watermark` 时跳过回填；初始快照失败时留在有界轮询，
 * 不把「未知水位」当成「已追平」。
 */
async function startWithStream(runtime: NotificationRuntime): Promise<void> {
  setPolling(runtime, true);
  const snapshot = await syncOnce(runtime);
  if (!runtime.started || runtime.visibility.hidden()) return;
  if (snapshot.errorCode !== null) {
    scheduleNext(runtime);
    return;
  }
  openStream(runtime);
}

/** 启动通知：有流端口时先 REST 快照再 SSE；否则走有界轮询。 */
function startPolling(runtime: NotificationRuntime): void {
  if (runtime.started) return;
  if (!runtime.deps.config.ready()) {
    runtime.core.errorCode = 'NOTIFICATIONS_NOT_READY';
    emit(runtime);
    return;
  }
  runtime.started = true;
  runtime.visibilityOff = runtime.visibility.subscribe(() => onVisibilityChange(runtime));
  if (runtime.deps.stream !== undefined) {
    void startWithStream(runtime).catch((error: unknown) => {
      reportFailure(runtime, error);
      kick(runtime);
    });
  } else {
    kick(runtime);
  }
}

function closePreviewRuntime(runtime: NotificationRuntime): void {
  runtime.previewEpoch += 1;
  releaseUrls(runtime);
  emit(runtime);
}

function disposeRuntime(runtime: NotificationRuntime): void {
  resetRuntime(runtime);
  runtime.listeners.clear();
  runtime.cached = null;
}

/** 构造通知控制器：一个页面一个实例；定时器/可见性/Blob URL 端口可注入（确定性驱动）。 */
export function createNotificationController(
  deps: NotificationControllerDeps
): NotificationController {
  const runtime = createRuntime(deps);
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    start: () => startPolling(runtime),
    stop: () => {
      stopLoop(runtime);
      emit(runtime);
    },
    sync: (options) => syncOnce(runtime, options),
    retry: (options) => syncOnce(runtime, options),
    markRead: (notificationId, options) => markReadOnce(runtime, notificationId, options),
    openPreview: (attachment, options) => openPreviewOnce(runtime, attachment, options),
    closePreview: () => closePreviewRuntime(runtime),
    reset: () => resetRuntime(runtime),
    dispose: () => disposeRuntime(runtime)
  };
}

/**
 * 装配自检（编译期）：真实控制器必须结构满足本 hook 的端口，页面装配不再自造适配层。
 * 端口或控制器任一改动破坏接线时 `tsc --noEmit` 直接失败。
 */
export type NotificationsPortCompatibility = [
  ChatAuthController extends NotificationsIdentityPort ? true : never,
  WebchatClient extends NotificationClientPort ? true : never
];

/** 配置接缝自检（编译期）：轮询间隔只从投影控制器取（本任务在 `useClientConfig` 透出 `limits()`）。 */
export type NotificationsConfigCompatibility = ClientConfigController extends {
  ready: boolean;
  limits(): WebchatClientLimits;
}
  ? true
  : never;

/** hook 消费的配置控制器面：`ClientConfigController` 结构满足。 */
export interface NotificationsConfigPort {
  ready: boolean;
  limits(): WebchatClientLimits;
}

/** hook 暴露给面板/页面的控制器：动作都是即发即忘，失败经 `onFailure` 与快照 `errorCode` 上报。 */
export interface NotificationsController {
  snapshot: NotificationSnapshot;
  start(): void;
  sync(): void;
  retry(): void;
  markRead(notificationId: string): void;
  openPreview(attachment: WebchatAttachment): void;
  closePreview(): void;
}

export interface NotificationsOptions {
  /** 展示缓存上界（页面持有，不是后端投影字段）。 */
  maxItems: number;
  activity?: NotificationActivityPort;
  onFailure?: (error: unknown) => void;
  /** 可见性端口（宿主/测试可注入）；缺省按 `document.visibilityState`。 */
  visibility?: NotificationVisibilityPort;
  /** SSE 通知流端口；缺省走有界轮询（测试/旧宿主）。 */
  stream?: NotificationStreamPort;
}

/** 点击处理器里的失败已由 `onFailure` 与快照 `errorCode` 上报，这里只兜住 rejection。 */
function fireAndForget(action: Promise<unknown>): void {
  action.catch(() => undefined);
}

/** 身份边界清场：退出/到期/撤销/冲突/切主体时复位控制器（清私有状态并撤销 Blob URL）。 */
function useNotificationIdentityCleanup(
  service: NotificationController,
  registerIdentityCleanup: (cleanup: () => void) => () => void
): void {
  useEffect(() => {
    const unregister = registerIdentityCleanup(() => {
      service.reset();
    });
    return () => {
      unregister();
    };
  }, [registerIdentityCleanup, service]);
}

/** 卸载释放：停轮询、注销可见性订阅并撤销预览 Blob URL。 */
function useNotificationDispose(service: NotificationController): void {
  useEffect(
    () => () => {
      service.dispose();
    },
    [service]
  );
}

/**
 * 一个页面一个控制器实例：`useMemo` 只依赖端口方法引用，并把身份边界清场与卸载释放挂在同一实例上。
 * 投影就绪与否参与依赖：就绪翻转时重建（尚未加载任何状态），不把旧投影的轮询参数留给新连接。
 */
function useNotificationService(
  client: NotificationClientPort,
  config: NotificationsConfigPort,
  auth: NotificationsIdentityPort,
  options: NotificationsOptions
): NotificationController {
  const { maxItems, activity, onFailure, visibility, stream } = options;
  const registerIdentityCleanup = auth.registerIdentityCleanup;
  const identitySignal = auth.identitySignal;
  const trackObjectUrl = auth.trackObjectUrl;
  const limits = config.limits;
  const ready = config.ready;
  const service = useMemo(
    () =>
      createNotificationController({
        client,
        config: { ready: () => ready, limits },
        identity: { registerIdentityCleanup, trackObjectUrl, identitySignal },
        maxItems,
        activity,
        onFailure,
        visibility,
        stream
      }),
    [
      client,
      ready,
      limits,
      registerIdentityCleanup,
      trackObjectUrl,
      identitySignal,
      maxItems,
      activity,
      onFailure,
      visibility,
      stream
    ]
  );
  useNotificationIdentityCleanup(service, registerIdentityCleanup);
  useNotificationDispose(service);
  return service;
}

/** 投影就绪后才启动轮询；就绪翻转/卸载都停轮询（重新就绪即重开，不带着旧参数继续跑）。 */
function useNotificationPolling(service: NotificationController, ready: boolean): void {
  useEffect(() => {
    if (!ready) return;
    service.start();
    return () => {
      service.stop();
    };
  }, [service, ready]);
}

/**
 * 绑定通知控制器与认证/配置控制器：`useSyncExternalStore` 直接读控制器快照，动作只经控制器；
 * 失败经 `onFailure` 与快照 `errorCode` 上报，点击处理器不产生未处理拒绝。
 */
export function useNotifications(
  client: NotificationClientPort,
  config: NotificationsConfigPort,
  auth: NotificationsIdentityPort,
  options: NotificationsOptions
): NotificationsController {
  const service = useNotificationService(client, config, auth, options);
  const snapshot = useSyncExternalStore(service.subscribe, service.snapshot, service.snapshot);
  useNotificationPolling(service, config.ready);

  const start = useCallback(() => service.start(), [service]);
  const sync = useCallback(() => {
    fireAndForget(service.sync());
  }, [service]);
  const retry = useCallback(() => {
    fireAndForget(service.retry());
  }, [service]);
  const markRead = useCallback(
    (notificationId: string) => {
      fireAndForget(service.markRead(notificationId));
    },
    [service]
  );
  const openPreview = useCallback(
    (attachment: WebchatAttachment) => {
      fireAndForget(service.openPreview(attachment));
    },
    [service]
  );
  const closePreview = useCallback(() => service.closePreview(), [service]);

  return { snapshot, start, sync, retry, markRead, openPreview, closePreview };
}
