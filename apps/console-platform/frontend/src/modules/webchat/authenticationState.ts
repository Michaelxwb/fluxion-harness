/**
 * CHAT 认证状态机与身份作用域（design frontend §3.4 `ChatAuthenticationService`、§3.5 `authentication`）。
 *
 * 一个页面只有一个当前 CHAT 主体，所以认证只在这里定义一次，展示组件与 hooks 都消费它：
 *
 * 1. **状态机**：`anonymous`（未认证）/ `exchanging`（显式交换中，singleflight）/ `authenticated` /
 *    `expired`（CHAT 会话或来源 Token 到期）/ `revoked`（Token/会话被撤销或用户停用）/
 *    `context-changed`（共享 Cookie 切用户导致的上下文冲突）。401 与 409 都**不自动重试**：
 *    到期回 Token 输入，冲突需用户明确重新进入；状态机没有任何定时器。
 * 2. **凭据卫生**：用户 Token 只作为一次 `exchangeToken` 调用的入参交给模块 service 放进交换请求体，
 *    不保存、不落持久缓存、不进状态快照；到期不滑动续期（不自动 `/me`、不自动交换、不续期）。
 * 3. **身份作用域**：每个身份边界（退出/到期/撤销/冲突/切主体）递增 `generation`、中止
 *    `identitySignal()`（回复订阅据此关流）、执行登记的消息/草稿/通知/游标清理，并撤销本身份创建
 *    的全部 Blob URL；边界之后完成的旧身份响应按 `generation` fence，不写新状态。
 * 4. **上下文唯一来源**：`setChatContextId` 只接受服务端验证过的 `auth_context_id`；`/me` 返回的
 *    上下文与已持有上下文不一致时判 `AUTH_CONTEXT_CHANGED`（TASK-071 遗留①），不静默换身份。
 *
 * 传输与错误码都来自模块 service（TASK-073）：本模块不拼装传输头、不读 Cookie、不打日志。
 */

import { setChatContextId } from '../../api/authScope';

import { webchatErrorCode } from './services/webchatApi';
import type {
  ChatAuthMethod,
  WebchatLogoutReceipt,
  WebchatRequestOptions,
  WebchatSessionInfo
} from './types';

/** 认证状态机的状态（design §3.5 `authentication.state`）。 */
export type ChatAuthState =
  | 'anonymous'
  | 'exchanging'
  | 'authenticated'
  | 'expired'
  | 'revoked'
  | 'context-changed';

/** 身份边界原因：清场是安全边界，原因只用于可观测与文案选择。 */
export type ChatIdentityEndReason =
  | 'logout'
  | 'unauthorized'
  | 'session-expired'
  | 'session-revoked'
  | 'context-changed'
  | 'identity-switch';

/**
 * 一次身份边界的现场。
 *
 * `failures` 由状态机在清理过程中原地填充：某个清理抛错不阻断其余清理（否则会漏数据），
 * 但也不静默——登记方与页面可在边界结束后读到全部失败。
 */
export interface ChatIdentityBoundary {
  reason: ChatIdentityEndReason;
  /** 边界之后的 generation：晚到的旧身份响应据此被 fence。 */
  generation: number;
  failures: unknown[];
}

/** 身份边界清理：消息/草稿/通知/游标/订阅等私有状态在这里释放。 */
export type ChatIdentityCleanup = (boundary: ChatIdentityBoundary) => void;

/** 页面级主体（design §3.5 `principal`）：字段名沿用服务端 wire 形状。 */
export interface ChatPrincipal {
  tenant_id: string;
  user_id: string;
  display_name: string;
  session_id: string;
}

/** 认证状态机快照：展示层唯一可读事实，不含 Token。 */
export interface ChatAuthenticationSnapshot {
  state: ChatAuthState;
  generation: number;
  principal: ChatPrincipal | null;
  authMethod: ChatAuthMethod | null;
  authContextId: string | null;
  expiresAt: string | null;
  /** 最近一次认证动作的稳定目录码；成功为 null。 */
  errorCode: string | null;
}

/** 认证端口：只取 `WebchatClient` 的三个认证方法，状态机因此没有任何写消息的能力。 */
export interface ChatAuthenticationClient {
  bootstrap(options?: WebchatRequestOptions): Promise<WebchatSessionInfo>;
  exchangeToken(token: string, options?: WebchatRequestOptions): Promise<WebchatSessionInfo>;
  logout(options?: WebchatRequestOptions): Promise<WebchatLogoutReceipt>;
}

export interface ChatAuthenticationDeps {
  client: ChatAuthenticationClient;
  /** 撤销 Blob URL 的实现（默认 `URL.revokeObjectURL`）；页面可注入以便观测。 */
  revokeObjectUrl?: (url: string) => void;
}

export interface ChatAuthenticationService {
  snapshot(): ChatAuthenticationSnapshot;
  subscribe(listener: (snapshot: ChatAuthenticationSnapshot) => void): () => void;
  /** `/me` 恢复：singleflight；同主体复核不产生身份边界，上下文变化判 `AUTH_CONTEXT_CHANGED`。 */
  restore(options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  /** 显式 Token 交换：singleflight；失败只记错误码，不自动重试、不自动互换 Cookie。 */
  exchangeToken(token: string, options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  /** 退出：撤本 CHAT 会话；无论服务端结果如何都清本地身份（失败只留错误码供重试）。 */
  logout(options?: WebchatRequestOptions): Promise<ChatAuthenticationSnapshot>;
  /** 共享传输的 CHAT 401 出口（注册到 `onChatUnauthorized`）。 */
  handleUnauthorized(): void;
  /** 调用方持有的稳定错误码收敛（409 冲突 / 401 撤销）；非认证错误返回 false 且不改状态。 */
  handleAuthFailure(error: unknown): boolean;
  generation(): number;
  /** 当前身份作用域的请求信号：退出/切主体即中止（订阅与在飞请求随之结束）。 */
  identitySignal(): AbortSignal;
  registerIdentityCleanup(cleanup: ChatIdentityCleanup): () => void;
  /** 登记本身份创建的 Blob URL；身份结束时统一撤销。 */
  trackObjectUrl(url: string): void;
  /** 释放监听与清理登记（页面卸载）；不改服务端会话。 */
  dispose(): void;
}

type ChatAuthListener = (snapshot: ChatAuthenticationSnapshot) => void;

interface AuthCore {
  state: ChatAuthState;
  session: WebchatSessionInfo | null;
  errorCode: string | null;
  generation: number;
  /** 身份边界序号：会话落地或结束都递增，用于 fence 边界之后完成的认证响应。 */
  epoch: number;
}

interface IdentityScope {
  controller: AbortController;
  cleanups: Set<ChatIdentityCleanup>;
  objectUrls: Set<string>;
}

/** 状态机运行时：核心状态、身份作用域、订阅与在飞认证动作（模块级函数的显式依赖）。 */
interface AuthRuntime {
  deps: ChatAuthenticationDeps;
  revokeObjectUrl: (url: string) => void;
  core: AuthCore;
  scope: IdentityScope;
  listeners: Set<ChatAuthListener>;
  cached: ChatAuthenticationSnapshot | null;
  restoreInFlight: Promise<ChatAuthenticationSnapshot> | null;
  exchangeInFlight: Promise<ChatAuthenticationSnapshot> | null;
}

const EXPIRED_CODES = new Set(['UNAUTHORIZED', 'CHAT_SESSION_EXPIRED', 'CHAT_TOKEN_EXPIRED']);
const REVOKED_CODES = new Set(['CHAT_SESSION_REVOKED', 'CHAT_TOKEN_REVOKED']);

function createRuntime(deps: ChatAuthenticationDeps): AuthRuntime {
  return {
    deps,
    revokeObjectUrl: deps.revokeObjectUrl ?? ((url: string) => URL.revokeObjectURL(url)),
    core: { state: 'anonymous', session: null, errorCode: null, generation: 0, epoch: 0 },
    scope: { controller: new AbortController(), cleanups: new Set(), objectUrls: new Set() },
    listeners: new Set(),
    cached: null,
    restoreInFlight: null,
    exchangeInFlight: null
  };
}

function principalOf(session: WebchatSessionInfo | null): ChatPrincipal | null {
  if (session === null) return null;
  return {
    tenant_id: session.tenant_id,
    user_id: session.user_id,
    display_name: session.display_name,
    session_id: session.session_id
  };
}

/** 快照按引用缓存：`useSyncExternalStore` 要求同一状态返回同一对象。 */
function currentSnapshot(runtime: AuthRuntime): ChatAuthenticationSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const { core } = runtime;
  runtime.cached = {
    state: core.state,
    generation: core.generation,
    principal: principalOf(core.session),
    authMethod: core.session?.auth_method ?? null,
    authContextId: core.session?.auth_context_id ?? null,
    expiresAt: core.session?.expires_at ?? null,
    errorCode: core.errorCode
  };
  return runtime.cached;
}

function emit(runtime: AuthRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function sameSubject(left: WebchatSessionInfo, right: WebchatSessionInfo): boolean {
  return left.tenant_id === right.tenant_id && left.user_id === right.user_id;
}

/** `/me` 失败的状态映射：只有明确的目录码才结束会话，传输失败保留现状以便重试。 */
function restoreFailureState(code: string): ChatAuthState | null {
  if (code === 'AUTH_CONTEXT_CHANGED') return 'context-changed';
  if (REVOKED_CODES.has(code)) return 'revoked';
  if (EXPIRED_CODES.has(code)) return 'expired';
  return null;
}

/** 交换失败的状态映射：无效/过期/限流留在 Token 输入，撤销明确提示联系管理员。 */
function exchangeFailureState(code: string): ChatAuthState {
  if (code === 'AUTH_CONTEXT_CHANGED') return 'context-changed';
  if (REVOKED_CODES.has(code)) return 'revoked';
  return 'anonymous';
}

function revokeObjectUrls(runtime: AuthRuntime): void {
  const urls = [...runtime.scope.objectUrls];
  runtime.scope.objectUrls.clear();
  for (const url of urls) runtime.revokeObjectUrl(url);
}

function runCleanup(cleanup: ChatIdentityCleanup, boundary: ChatIdentityBoundary): void {
  try {
    cleanup(boundary);
  } catch (error) {
    boundary.failures.push(error);
  }
}

/** 释放当前身份：先撤上下文，再停信号、清私有状态、撤销 Blob URL（顺序即安全边界）。 */
function releaseIdentity(runtime: AuthRuntime, reason: ChatIdentityEndReason): void {
  setChatContextId(null);
  runtime.scope.controller.abort();
  runtime.scope.controller = new AbortController();
  const boundary: ChatIdentityBoundary = {
    reason,
    generation: runtime.core.generation,
    failures: []
  };
  for (const cleanup of [...runtime.scope.cleanups]) runCleanup(cleanup, boundary);
  revokeObjectUrls(runtime);
}

/** 结束会话：有真实会话才构成身份边界（generation/清理只发生一次）。 */
function endSession(
  runtime: AuthRuntime,
  reason: ChatIdentityEndReason,
  state: ChatAuthState,
  errorCode: string | null
): void {
  const { core } = runtime;
  const hadSession = core.session !== null;
  const changed = core.state !== state || core.errorCode !== errorCode;
  core.session = null;
  core.state = state;
  core.errorCode = errorCode;
  if (!hadSession) {
    setChatContextId(null);
    if (changed) emit(runtime);
    return;
  }
  core.epoch += 1;
  core.generation += 1;
  releaseIdentity(runtime, reason);
  emit(runtime);
}

function adoptSession(runtime: AuthRuntime, session: WebchatSessionInfo): void {
  const { core } = runtime;
  if (core.session !== null && !sameSubject(core.session, session)) {
    core.generation += 1;
    releaseIdentity(runtime, 'identity-switch');
  }
  core.session = session;
  core.state = 'authenticated';
  core.errorCode = null;
  core.epoch += 1;
  setChatContextId(session.auth_context_id);
  emit(runtime);
}

/** 落地验证结果；边界之后迟到的结果按 epoch fence，不复活旧会话。 */
function applyVerifiedSession(
  runtime: AuthRuntime,
  session: WebchatSessionInfo,
  epoch: number,
  source: 'restore' | 'exchange'
): ChatAuthenticationSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const held = runtime.core.session?.auth_context_id ?? null;
  if (source === 'restore' && held !== null && held !== session.auth_context_id) {
    endSession(runtime, 'context-changed', 'context-changed', 'AUTH_CONTEXT_CHANGED');
    return currentSnapshot(runtime);
  }
  adoptSession(runtime, session);
  return currentSnapshot(runtime);
}

function applyRestoreFailure(
  runtime: AuthRuntime,
  error: unknown,
  epoch: number
): ChatAuthenticationSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const code = webchatErrorCode(error);
  const state = restoreFailureState(code);
  if (state === null) {
    runtime.core.errorCode = code;
    emit(runtime);
    return currentSnapshot(runtime);
  }
  const reason: ChatIdentityEndReason =
    state === 'context-changed'
      ? 'context-changed'
      : state === 'revoked'
        ? 'session-revoked'
        : 'session-expired';
  endSession(runtime, reason, state, code);
  return currentSnapshot(runtime);
}

function applyExchangeFailure(
  runtime: AuthRuntime,
  error: unknown,
  epoch: number
): ChatAuthenticationSnapshot {
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  const code = webchatErrorCode(error);
  if (runtime.core.session !== null) {
    // 已有有效会话：失败的交换不结束它，只把稳定码留给 Token 输入。
    runtime.core.state = 'authenticated';
    runtime.core.errorCode = code;
    emit(runtime);
    return currentSnapshot(runtime);
  }
  const state = exchangeFailureState(code);
  if (state === 'anonymous') {
    runtime.core.state = 'anonymous';
    runtime.core.errorCode = code;
    emit(runtime);
    return currentSnapshot(runtime);
  }
  endSession(runtime, state === 'context-changed' ? 'context-changed' : 'session-revoked', state, code);
  return currentSnapshot(runtime);
}

/** `/me` 恢复：并发调用合并为一次请求（singleflight），结果共享。 */
function restoreSession(
  runtime: AuthRuntime,
  options: WebchatRequestOptions = {}
): Promise<ChatAuthenticationSnapshot> {
  if (runtime.restoreInFlight !== null) return runtime.restoreInFlight;
  const epoch = runtime.core.epoch;
  const action = runtime.deps.client.bootstrap(options).then(
    (session) => applyVerifiedSession(runtime, session, epoch, 'restore'),
    (error: unknown) => applyRestoreFailure(runtime, error, epoch)
  );
  const settled = action.finally(() => {
    runtime.restoreInFlight = null;
  });
  runtime.restoreInFlight = settled;
  return settled;
}

/** 显式 Token 交换：并发提交合并为一次请求；未认证时才显示交换中。 */
function exchangeChatToken(
  runtime: AuthRuntime,
  token: string,
  options: WebchatRequestOptions = {}
): Promise<ChatAuthenticationSnapshot> {
  if (runtime.exchangeInFlight !== null) return runtime.exchangeInFlight;
  const epoch = runtime.core.epoch;
  if (runtime.core.session === null) {
    runtime.core.state = 'exchanging';
    runtime.core.errorCode = null;
    emit(runtime);
  }
  const action = runtime.deps.client.exchangeToken(token, options).then(
    (session) => applyVerifiedSession(runtime, session, epoch, 'exchange'),
    (error: unknown) => applyExchangeFailure(runtime, error, epoch)
  );
  const settled = action.finally(() => {
    runtime.exchangeInFlight = null;
  });
  runtime.exchangeInFlight = settled;
  return settled;
}

/** 退出：本地清场是安全边界；服务端撤销失败只留稳定码，页面可重试。 */
async function logoutSession(
  runtime: AuthRuntime,
  options: WebchatRequestOptions = {}
): Promise<ChatAuthenticationSnapshot> {
  const epoch = runtime.core.epoch;
  let errorCode: string | null = null;
  try {
    await runtime.deps.client.logout(options);
  } catch (error) {
    errorCode = webchatErrorCode(error);
  }
  if (epoch !== runtime.core.epoch) return currentSnapshot(runtime);
  endSession(runtime, 'logout', 'anonymous', errorCode);
  return currentSnapshot(runtime);
}

/** 稳定错误码收敛：409 冲突与 401 撤销/到期都能被页面精确表达。 */
function applyAuthFailure(runtime: AuthRuntime, error: unknown): boolean {
  const code = webchatErrorCode(error);
  if (code === 'AUTH_CONTEXT_CHANGED') {
    endSession(runtime, 'context-changed', 'context-changed', code);
    return true;
  }
  if (REVOKED_CODES.has(code)) {
    endSession(runtime, 'session-revoked', 'revoked', code);
    return true;
  }
  if (EXPIRED_CODES.has(code)) {
    endSession(runtime, 'session-expired', 'expired', code);
    return true;
  }
  return false;
}

/** 构造 CHAT 认证状态机；一个页面一个实例，测试可注入 Blob URL 撤销记录器。 */
export function createChatAuthenticationService(
  deps: ChatAuthenticationDeps
): ChatAuthenticationService {
  const runtime = createRuntime(deps);
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    restore: (options) => restoreSession(runtime, options),
    exchangeToken: (token, options) => exchangeChatToken(runtime, token, options),
    logout: (options) => logoutSession(runtime, options),
    handleUnauthorized: () => {
      endSession(runtime, 'unauthorized', 'expired', 'UNAUTHORIZED');
    },
    handleAuthFailure: (error) => applyAuthFailure(runtime, error),
    generation: () => runtime.core.generation,
    identitySignal: () => runtime.scope.controller.signal,
    registerIdentityCleanup(cleanup) {
      runtime.scope.cleanups.add(cleanup);
      return () => {
        runtime.scope.cleanups.delete(cleanup);
      };
    },
    trackObjectUrl(url) {
      runtime.scope.objectUrls.add(url);
    },
    dispose() {
      runtime.listeners.clear();
      runtime.scope.cleanups.clear();
      runtime.scope.controller.abort();
    }
  };
}
