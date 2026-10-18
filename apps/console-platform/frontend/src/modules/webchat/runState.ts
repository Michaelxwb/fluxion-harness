/**
 * 活跃 Run 的分代状态与回复订阅编排（design frontend §3.4 `StreamingReply`、§3.5 `activeRun`/`connection`；
 * TASK-082）。
 *
 * 这一层是「可见 Run」的唯一状态机：订阅生命周期、分代正文、终态收敛与显式恢复只在这里定义一次。
 *
 * 1. **只订阅可见 Run**：`watch(target)` 只接受调用方（`useRunEvents`）给出的可见目标；同一目标幂等
 *    （每页面/当前 Run 至多一条订阅），换 Run/换会话先关旧订阅并按 `generation` fence 迟到帧与旧结束
 *    原因，终态 Run 不重新打开。本模块结构上没有任何 POST 能力：重连只发生在共享流层的 GET 上，
 *    退避/抖动/次数/单帧预算都消费 API-19 投影（`newConnectionConfig()`），不复制默认值。
 * 2. **分代正文**：`message.delta` 追加与 `message.snapshot` 整组原子替换由共享收集器完成；本层只把
 *    已应用结果落到 `text`/`replyGeneration`，并在应用成功后调用 `noteAppliedSeq` 推进会话层游标
 *    （checkpoint 不推进已应用水位、重复 seq 与旧代帧被显式忽略，绝不把消息页 `through_seq` 当游标）。
 * 3. **终态收敛与 EOF**：只有持久终态（`run.terminal` 或终态 `run.stage`）改变业务状态；EOF 不是完成，
 *    由共享流层按投影重连。重连预算耗尽、运行错误、游标过期与认证失效各自留下显式状态与显式动作
 *    （`retry()`/`recover()`），不静默重试、不自动补拉；认证失效只等重新进入，本层不自动重开。
 * 4. **按会话有界暂存**：Run 状态（正文/阶段/确认/附件/已应用水位）按 `conversationId` 保存并按最近
 *    使用淘汰（当前订阅会话不淘汰）；切回同一 Run 直接从缓存恢复正文、从本地游标续传，不重复、不丢。
 *    身份边界 `reset()` 整体清空。
 *
 * 本文件不发 HTTP（订阅只经共享流层 `src/api/stream.ts` 的 GET 出口）、不拼装传输头、不读 Cookie、
 * 不落持久存储、不打日志、不引用 React、不持有文案键。
 */

import { openReplyStream } from '../../api/stream';
import type {
  ReplyStreamUpdate,
  WebchatConnectionAttempt,
  WebchatCursorWindow,
  WebchatReplyStop,
  WebchatReplyStream
} from '../../api/stream';

import { ClientConfigError } from './clientConfig';
import { webchatErrorCode } from './services/webchatApi';
import type {
  RunStatus,
  WebchatAttachment,
  WebchatClientConfig,
  WebchatConfirmation,
  WebchatConversationDetail,
  WebchatExecutionStatus
} from './types';

/** 本地稳定码：目标非法/暂存上界非法时显式失败（不构造无归属的订阅）。 */
export type RunStateErrorCode = 'RUN_TARGET_INVALID' | 'RUN_SCOPE_LIMIT_INVALID';

/** 状态层本地显式失败：稳定码 + 无 HTTP 副作用。 */
export class RunStateError extends Error {
  readonly code: RunStateErrorCode;

  constructor(code: RunStateErrorCode) {
    super(code);
    this.name = 'RunStateError';
    this.code = code;
  }
}

/** 连接状态：只描述当前订阅，不描述服务端 Run（终态由 `status` 表达）。 */
export type RunConnectionState =
  | 'idle'
  | 'connecting'
  | 'open'
  | 'reconnecting'
  | 'closed'
  | 'failed'
  | 'auth-required'
  | 'cursor-recovery';

/** design §3.5 `connection`：每页面/当前 Run 至多一条订阅，断流只重连 GET。 */
export interface RunConnectionSnapshot {
  state: RunConnectionState;
  /** 自上次有进展以来的连续重连次数（首连为 0；退避与次数只来自 API-19 投影）。 */
  retries: number;
  /** 最近一次显式错误码（本地边界码或服务端稳定目录码）；无错误为 null。 */
  lastError: string | null;
  /** 当前连接创建时冻结的 `transport_revision`（活跃连接不因新配置中途换规则）。 */
  transportRevision: string | null;
  /** 游标恢复窗口（仅 `cursor-recovery` 非空；服务端窗口原样带出）。 */
  cursor: WebchatCursorWindow | null;
}

/** 显式恢复要求：订阅停止后必须由调用方显式动作才能重开（不自动补拉、不静默重试）。 */
export interface RunRecoveryState {
  kind: 'retry' | 'cursor' | 'auth';
  /** 停止原因码（本地边界码或服务端稳定目录码）。 */
  errorCode: string;
  /** 游标恢复窗口（仅 `cursor` 非空）。 */
  cursor: WebchatCursorWindow | null;
}

/** 会话内的活跃 Run 状态（design §3.5 `activeRun`）：正文/阶段/确认/附件与已应用水位。 */
export interface ActiveRunState {
  conversationId: string;
  runId: string;
  submissionId: string | null;
  status: RunStatus;
  executionStatus: WebchatExecutionStatus | null;
  confirmation: WebchatConfirmation | null;
  attachments: WebchatAttachment[];
  /** 已应用水位：只在事件应用成功后单调推进（checkpoint 不推进）。 */
  lastAppliedSeq: number;
  replyGeneration: number | null;
  text: string;
  errorCode: string | null;
  retryable: boolean | null;
  /** 需显式动作才能重开的停止事实；null = 可继续（未停止/正常关闭）。 */
  recovery: RunRecoveryState | null;
}

/** 展示阶段（design §3.6 发送/回复）：等待与执行计时分开，终态/等待输入停止活动计时。 */
export type RunReplyPhase = 'queued' | 'running' | 'waiting-tool' | 'waiting-input' | 'terminal';

/** design §3.4 `StreamingReply` 的 props：展示组件只吃这些事实，不自行推断阶段或计时。 */
export interface StreamingReplyProps {
  runId: string;
  text: string;
  phase: RunReplyPhase;
  queuedAt: string | null;
  queueReason: string | null;
  queueDeadlineAt: string | null;
  startedAt: string | null;
  connectionState: RunConnectionState;
}

/** 订阅目标：可见会话的活跃 Run 事实（API-15 详情或受理回执），身份由调用方持有。 */
export interface RunSubscriptionTarget {
  conversationId: string;
  runId: string;
  submissionId: string | null;
  status: RunStatus;
  executionStatus: WebchatExecutionStatus | null;
  confirmation: WebchatConfirmation | null;
}

export interface RunEventsSnapshot {
  run: ActiveRunState | null;
  connection: RunConnectionSnapshot;
  /** `run === null` 时为 null：无可见 Run 不渲染空占位助手正文。 */
  streaming: StreamingReplyProps | null;
}

/** 会话层端口：视图信号与按会话的已应用游标（TASK-078），本层不另存一份游标。 */
export interface RunEventConversationPort {
  /** 当前视图请求信号：入口/会话/身份边界变化即中止（旧订阅据此关流）。 */
  viewSignal(): AbortSignal;
  appliedSeq(conversationId: string, runId: string): number | null;
  noteAppliedSeq(conversationId: string, runId: string, seq: number): void;
}

/** 配置端口：只取 API-19 投影的冻结构造（未就绪时显式失败，不回落默认）。 */
export interface RunEventControllerConfig {
  newConnectionConfig(): WebchatClientConfig;
}

export interface RunEventControllerDeps {
  conversations: RunEventConversationPort;
  config: RunEventControllerConfig;
  /** 按会话暂存上界：超出按最近使用淘汰（当前订阅会话永不淘汰）。 */
  maxScopedRuns: number;
  /** 抖动源（默认 Math.random）；退避/次数只来自 API-19 投影。 */
  random?: () => number;
}

export interface RunEventController {
  snapshot(): RunEventsSnapshot;
  subscribe(listener: (snapshot: RunEventsSnapshot) => void): () => void;
  /** 订阅可见 Run（null = 无可见 Run）：同目标幂等，换目标先关旧订阅并 fence 迟到帧。 */
  watch(target: RunSubscriptionTarget | null): void;
  /** 显式重开当前 Run 的订阅（重连预算耗尽/运行错误后的重试动作；仍只发 GET）。 */
  retry(): void;
  /** 显式游标恢复：本地游标已失效，从服务端安全快照重开（仍只发 GET，不重发 POST）。 */
  recover(): void;
  /** 关流（卸载/切视图）；不取消服务端 Run。 */
  close(): void;
  /** 身份边界清场：清空按会话暂存与订阅。 */
  reset(): void;
  dispose(): void;
}

type RunEventsListener = (snapshot: RunEventsSnapshot) => void;

const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  'COMPLETED',
  'FAILED',
  'CANCELLED'
]);
const TERMINAL_STAGES = new Set<WebchatExecutionStatus['stage']>([
  'COMPLETED',
  'FAILED',
  'CANCELLED'
]);
/** 已应用更新：会推进已应用水位的事件（分片未齐与 checkpoint 不在其列）。 */
type AppliedUpdate = Extract<
  ReplyStreamUpdate,
  { kind: 'delta' | 'snapshot' | 'stage' | 'terminal' | 'error' | 'confirmation' | 'artifact' }
>;

/** 终态 Run 不重新打开（`muad_contracts.enums.TerminalStatus`）。 */
export function isTerminalRunStatus(status: RunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** 展示阶段：排队/执行/等待工具/等待输入/终态只由持久状态与持久阶段派生。 */
export function replyPhase(
  status: RunStatus,
  executionStatus: WebchatExecutionStatus | null
): RunReplyPhase {
  const stage = executionStatus === null ? null : executionStatus.stage;
  if (isTerminalRunStatus(status) || (stage !== null && TERMINAL_STAGES.has(stage))) {
    return 'terminal';
  }
  if (stage === 'WAITING_INPUT' || status === 'WAITING_INPUT') return 'waiting-input';
  if (stage === 'WAITING_TOOL' || status === 'WAITING_TOOL') return 'waiting-tool';
  if (stage === 'RUNNING' || status === 'RUNNING') return 'running';
  return 'queued';
}

/** `StreamingReply` props 投影：等待/执行计时只取持久阶段字段，无可见 Run 时为 null。 */
export function streamingReply(
  run: ActiveRunState | null,
  connection: RunConnectionSnapshot
): StreamingReplyProps | null {
  if (run === null) return null;
  const status = run.executionStatus;
  return {
    runId: run.runId,
    text: run.text,
    phase: replyPhase(run.status, status),
    queuedAt: status === null ? null : status.queued_at,
    queueReason: status === null ? null : status.reason,
    queueDeadlineAt: status === null ? null : status.deadline_at,
    startedAt: status === null ? null : status.started_at,
    connectionState: connection.state
  };
}

/** 可见 Run 目标：只由所选会话的权威详情派生（无会话/无活跃 Run/终态都不订阅）。 */
export function visibleRunTarget(selection: {
  conversationId: string | null;
  conversation: WebchatConversationDetail | null;
}): RunSubscriptionTarget | null {
  const { conversationId, conversation } = selection;
  if (conversationId === null || conversation === null) return null;
  const active = conversation.active_run;
  if (active === null) return null;
  return {
    conversationId,
    runId: active.id,
    submissionId: null,
    status: active.status,
    executionStatus: active.execution_status,
    confirmation: active.confirmation
  };
}

function idleConnection(): RunConnectionSnapshot {
  return {
    state: 'idle',
    retries: 0,
    lastError: null,
    transportRevision: null,
    cursor: null
  };
}

function requireTarget(target: RunSubscriptionTarget): RunSubscriptionTarget {
  const conversationId = target.conversationId.trim();
  const runId = target.runId.trim();
  if (conversationId === '' || runId === '') throw new RunStateError('RUN_TARGET_INVALID');
  return { ...target, conversationId, runId };
}

/** 首次观察到的 Run 事实：只信调用方给出的持久状态/阶段/确认，不补默认值。 */
function initialRunState(target: RunSubscriptionTarget): ActiveRunState {
  return {
    conversationId: target.conversationId,
    runId: target.runId,
    submissionId: target.submissionId,
    status: target.status,
    executionStatus: target.executionStatus,
    confirmation: target.confirmation,
    attachments: [],
    lastAppliedSeq: 0,
    replyGeneration: null,
    text: '',
    errorCode: null,
    retryable: null,
    recovery: null
  };
}

/** 同一 Run 再次被观察：只刷新更权威的事实，绝不把已收敛的终态降级回活跃。 */
function refreshRunFacts(run: ActiveRunState, target: RunSubscriptionTarget): void {
  if (target.submissionId !== null) run.submissionId = target.submissionId;
  if (!isTerminalRunStatus(run.status) && target.status !== run.status) run.status = target.status;
  if (target.executionStatus !== null) run.executionStatus = target.executionStatus;
  if (target.confirmation !== null) run.confirmation = target.confirmation;
}

/** 附件按 `artifact_id` 身份合并（重复事件不重复累计，新版本覆盖旧版本）。 */
function mergeAttachments(
  existing: readonly WebchatAttachment[],
  incoming: WebchatAttachment
): WebchatAttachment[] {
  const byId = new Map<string, WebchatAttachment>();
  for (const item of existing) byId.set(item.artifact_id, item);
  byId.set(incoming.artifact_id, incoming);
  return [...byId.values()];
}

/** 阶段事件：持久阶段同时驱动业务状态（等待输入与终态才改变状态，排队不假装执行）。 */
function applyStage(run: ActiveRunState, status: WebchatExecutionStatus): void {
  run.executionStatus = status;
  if (status.stage !== 'WAITING_INPUT') run.confirmation = null; // 确认只在等待输入阶段有效
  if (TERMINAL_STAGES.has(status.stage)) run.status = status.stage as RunStatus;
  else if (status.stage === 'WAITING_INPUT' || status.stage === 'WAITING_TOOL') {
    run.status = status.stage;
  }
}

function recoveryFromStop(stop: WebchatReplyStop): RunRecoveryState | null {
  if (stop.kind === 'cursor-recovery') {
    return { kind: 'cursor', errorCode: stop.error.code, cursor: stop.error.cursor };
  }
  if (stop.kind === 'auth-required') {
    return { kind: 'auth', errorCode: stop.error.code, cursor: null };
  }
  if (stop.kind === 'failed') return { kind: 'retry', errorCode: stop.error.code, cursor: null };
  if (stop.kind === 'run-error') return { kind: 'retry', errorCode: stop.code, cursor: null };
  return null;
}

function connectionForRecovery(
  recovery: RunRecoveryState,
  retries: number
): RunConnectionSnapshot {
  const state: RunConnectionState =
    recovery.kind === 'cursor' ? 'cursor-recovery' : recovery.kind === 'auth' ? 'auth-required' : 'failed';
  return {
    state,
    retries,
    lastError: recovery.errorCode,
    transportRevision: null,
    cursor: recovery.cursor
  };
}

interface RunSubscriptionRuntime {
  conversationId: string;
  runId: string;
  /** 本次 attempt 的代号：换订阅/重开都递增，旧 attempt 的迟到帧与结束据此被 fence。 */
  generation: number;
  stream: WebchatReplyStream | null;
  /** 本地游标已失效（显式游标恢复）：本次订阅从服务端安全快照重开。 */
  recovery: boolean;
  /** 订阅已结束（终态/失败/关流）：结束之后到达的迟到帧不再写状态。 */
  stopped: boolean;
  /** 视图信号中止监听（换订阅/关流时移除，不累积监听）。 */
  detachView: (() => void) | null;
}

interface RunEventsRuntime {
  deps: RunEventControllerDeps;
  runs: Map<string, ActiveRunState>;
  touched: Map<string, number>;
  touchCounter: number;
  /** 当前订阅的会话（null = 无可见 Run）。 */
  visible: string | null;
  connection: RunConnectionSnapshot;
  subscription: RunSubscriptionRuntime | null;
  generation: number;
  listeners: Set<RunEventsListener>;
  cached: RunEventsSnapshot | null;
}

function visibleRun(runtime: RunEventsRuntime): ActiveRunState | null {
  if (runtime.visible === null) return null;
  return runtime.runs.get(runtime.visible) ?? null;
}

function currentSnapshot(runtime: RunEventsRuntime): RunEventsSnapshot {
  if (runtime.cached !== null) return runtime.cached;
  const run = visibleRun(runtime);
  runtime.cached = {
    run,
    connection: runtime.connection,
    streaming: streamingReply(run, runtime.connection)
  };
  return runtime.cached;
}

function emit(runtime: RunEventsRuntime): void {
  runtime.cached = null;
  const snapshot = currentSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

/** 连接状态落地：内容不变时不产生新快照（订阅者不因重复上报重渲染）。 */
function setConnection(runtime: RunEventsRuntime, next: RunConnectionSnapshot): void {
  const current = runtime.connection;
  if (
    current.state === next.state &&
    current.retries === next.retries &&
    current.lastError === next.lastError &&
    current.transportRevision === next.transportRevision &&
    current.cursor === next.cursor
  ) {
    return;
  }
  runtime.connection = next;
  emit(runtime);
}

/** 最近使用淘汰：超出上界时删最小 touched 的会话；当前订阅会话永不淘汰。 */
function evictRuns(runtime: RunEventsRuntime, keep: string): void {
  while (runtime.runs.size > runtime.deps.maxScopedRuns) {
    let victim: string | null = null;
    let oldest = Number.POSITIVE_INFINITY;
    for (const [id, touched] of runtime.touched) {
      if (id === keep || id === runtime.visible) continue;
      if (touched < oldest) {
        oldest = touched;
        victim = id;
      }
    }
    if (victim === null) return;
    runtime.runs.delete(victim);
    runtime.touched.delete(victim);
  }
}

function touchRun(runtime: RunEventsRuntime, target: RunSubscriptionTarget): ActiveRunState {
  runtime.touchCounter += 1;
  runtime.touched.set(target.conversationId, runtime.touchCounter);
  const existing = runtime.runs.get(target.conversationId);
  if (existing !== undefined && existing.runId === target.runId) {
    refreshRunFacts(existing, target);
    return existing;
  }
  const created = initialRunState(target);
  runtime.runs.set(target.conversationId, created);
  evictRuns(runtime, target.conversationId);
  return created;
}

function noteApplied(
  runtime: RunEventsRuntime,
  sub: RunSubscriptionRuntime,
  run: ActiveRunState,
  seq: number
): void {
  if (seq <= run.lastAppliedSeq) return;
  run.lastAppliedSeq = seq;
  sub.recovery = false; // 已从安全快照续上：本地游标重新可用
  runtime.deps.conversations.noteAppliedSeq(run.conversationId, run.runId, seq);
}

function applyApplied(
  runtime: RunEventsRuntime,
  sub: RunSubscriptionRuntime,
  run: ActiveRunState,
  update: AppliedUpdate
): void {
  if (update.kind === 'delta' || update.kind === 'snapshot') {
    run.text = update.text;
    run.replyGeneration = update.replyGeneration;
  } else if (update.kind === 'stage') {
    applyStage(run, update.data);
  } else if (update.kind === 'terminal') {
    run.status = update.status;
  } else if (update.kind === 'error') {
    run.errorCode = update.code;
    run.retryable = update.retryable;
  } else if (update.kind === 'confirmation') {
    run.confirmation = update.data;
  } else {
    run.attachments = mergeAttachments(run.attachments, update.data);
  }
  noteApplied(runtime, sub, run, update.seq);
}

/** 应用一个已解析帧：旧 attempt 迟到帧与已结束订阅被 fence；ignored/checkpoint/未齐分片不推水位。 */
function applyUpdate(runtime: RunEventsRuntime, generation: number, update: ReplyStreamUpdate): void {
  const sub = runtime.subscription;
  if (sub === null || sub.generation !== generation || sub.stopped) return;
  const run = runtime.runs.get(sub.conversationId);
  if (run === undefined || run.runId !== sub.runId) return;
  if (update.kind === 'ignored') return;
  const opened = runtime.connection.state !== 'open';
  if (opened) {
    runtime.connection = { ...runtime.connection, state: 'open', cursor: null };
  }
  if (update.kind === 'checkpoint' || update.kind === 'snapshot-part') {
    if (opened) emit(runtime);
    return;
  }
  applyApplied(runtime, sub, run, update);
  emit(runtime);
}

/** 一次连接尝试的显式上报：重连（仍 GET）与首连都可见，退避数值仍只来自投影。 */
function applyAttempt(
  runtime: RunEventsRuntime,
  generation: number,
  attempt: WebchatConnectionAttempt
): void {
  const sub = runtime.subscription;
  if (sub === null || sub.generation !== generation || sub.stopped) return;
  setConnection(runtime, {
    ...runtime.connection,
    state: attempt.retries > 0 ? 'reconnecting' : 'connecting',
    retries: attempt.retries,
    lastError: null,
    cursor: null
  });
}

/** 订阅结束：旧 attempt 的结束不写新状态；终态收敛、失败/过期/失效留下显式恢复要求。 */
function applyStop(runtime: RunEventsRuntime, generation: number, stop: WebchatReplyStop): void {
  const sub = runtime.subscription;
  if (sub === null || sub.generation !== generation) return;
  sub.stream = null;
  sub.stopped = true;
  const run = runtime.runs.get(sub.conversationId);
  if (run !== undefined && run.runId === sub.runId) {
    if (stop.kind === 'terminal') run.status = stop.status;
    else {
      if (stop.kind === 'run-error') {
        run.errorCode = stop.code;
        run.retryable = stop.retryable;
      }
      run.recovery = recoveryFromStop(stop);
    }
  }
  runtime.connection =
    stop.kind === 'terminal' || stop.kind === 'closed'
      ? { ...runtime.connection, state: 'closed', cursor: null }
      : {
          ...runtime.connection,
          ...recoveryConnectionPatch(stop),
          retries: runtime.connection.retries
        };
  emit(runtime);
}

/** 非正常结束的连接状态补丁：恢复要求原样落到连接快照（服务端游标窗口原样带出）。 */
function recoveryConnectionPatch(stop: WebchatReplyStop): Partial<RunConnectionSnapshot> {
  const recovery = recoveryFromStop(stop);
  if (recovery === null) return { state: 'closed', cursor: null };
  return connectionForRecovery(recovery, 0);
}

/** 视图信号中止（入口/会话/身份边界变化）：同步关旧流并清可见 Run，不把旧会话正文留给新视图。 */
function handleViewAbort(
  runtime: RunEventsRuntime,
  sub: RunSubscriptionRuntime,
  generation: number
): void {
  if (runtime.subscription !== sub || sub.generation !== generation) return;
  detachSubscription(runtime, sub);
  runtime.visible = null;
  setConnection(runtime, idleConnection());
}

function detachSubscription(runtime: RunEventsRuntime, sub: RunSubscriptionRuntime): void {
  runtime.generation += 1;
  if (runtime.subscription === sub) runtime.subscription = null;
  sub.stopped = true;
  sub.detachView?.();
  sub.detachView = null;
  sub.stream?.close();
  sub.stream = null;
}

function attachViewAbort(
  runtime: RunEventsRuntime,
  sub: RunSubscriptionRuntime,
  signal: AbortSignal,
  generation: number
): () => void {
  const handler = (): void => {
    handleViewAbort(runtime, sub, generation);
  };
  if (signal.aborted) {
    handler();
    return () => undefined;
  }
  signal.addEventListener('abort', handler, { once: true });
  return () => {
    signal.removeEventListener('abort', handler);
  };
}

/** 配置投影未就绪/非法：显式失败（零请求、不回落默认）并留下显式重试要求。 */
function failWithoutConfig(runtime: RunEventsRuntime, sub: RunSubscriptionRuntime, code: string): void {
  const run = runtime.runs.get(sub.conversationId);
  if (run !== undefined && run.runId === sub.runId) {
    run.recovery = { kind: 'retry', errorCode: code, cursor: null };
  }
  sub.stopped = true;
  setConnection(runtime, {
    state: 'failed',
    retries: 0,
    lastError: code,
    transportRevision: null,
    cursor: null
  });
}

/** 打开订阅：配置投影未就绪/非法时显式失败（零请求），连接冻结版本与传输参数。 */
function openStream(runtime: RunEventsRuntime, sub: RunSubscriptionRuntime): void {
  let config: WebchatClientConfig;
  try {
    config = runtime.deps.config.newConnectionConfig();
  } catch (error) {
    failWithoutConfig(
      runtime,
      sub,
      error instanceof ClientConfigError ? error.code : webchatErrorCode(error)
    );
    return;
  }
  const generation = sub.generation;
  const run = runtime.runs.get(sub.conversationId);
  const local = runtime.deps.conversations.appliedSeq(sub.conversationId, sub.runId);
  const signal = runtime.deps.conversations.viewSignal();
  sub.stopped = false;
  const stream = openReplyStream({
    runId: sub.runId,
    config,
    afterSeq: sub.recovery ? 0 : (local ?? 0),
    // 重开恢复：本地已应用正文/代号交回共享收集器（同一处事实），游标失效时由服务端安全快照重建。
    text: sub.recovery || run === undefined ? '' : run.text,
    replyGeneration: sub.recovery || run === undefined ? null : run.replyGeneration,
    signal,
    random: runtime.deps.random,
    onAttempt: (attempt) => {
      applyAttempt(runtime, generation, attempt);
    },
    onUpdate: (update) => {
      applyUpdate(runtime, generation, update);
    }
  });
  sub.stream = stream;
  sub.detachView = attachViewAbort(runtime, sub, signal, generation);
  setConnection(runtime, {
    state: 'connecting',
    retries: 0,
    lastError: null,
    transportRevision: stream.transportRevision,
    cursor: null
  });
  void stream.done.then((stop) => {
    applyStop(runtime, generation, stop);
  });
}

/** 重开当前订阅（显式动作）：递增 generation 并移除旧监听，旧 attempt 的迟到帧随即失效。 */
function reopen(runtime: RunEventsRuntime, sub: RunSubscriptionRuntime): void {
  sub.detachView?.();
  sub.detachView = null;
  runtime.generation += 1;
  sub.generation = runtime.generation;
  runtime.subscription = sub;
  openStream(runtime, sub);
}

function closeSubscription(runtime: RunEventsRuntime, state: 'idle' | 'closed'): void {
  const sub = runtime.subscription;
  if (sub !== null) detachSubscription(runtime, sub);
  runtime.connection = { ...idleConnection(), state };
  emit(runtime);
}

function beginSubscription(runtime: RunEventsRuntime, target: RunSubscriptionTarget): void {
  closeSubscription(runtime, 'idle');
  runtime.visible = target.conversationId;
  const run = touchRun(runtime, target);
  const sub: RunSubscriptionRuntime = {
    conversationId: target.conversationId,
    runId: target.runId,
    generation: runtime.generation,
    stream: null,
    recovery: false,
    stopped: false,
    detachView: null
  };
  runtime.subscription = sub;
  emit(runtime);
  if (isTerminalRunStatus(run.status)) {
    runtime.connection = { ...idleConnection(), state: 'closed' }; // 终态 Run 不重新打开
    emit(runtime);
    return;
  }
  if (run.recovery !== null) {
    runtime.connection = connectionForRecovery(run.recovery, 0); // 需显式动作，不自动重开
    emit(runtime);
    return;
  }
  openStream(runtime, sub);
}

function watch(runtime: RunEventsRuntime, target: RunSubscriptionTarget | null): void {
  if (target === null) {
    closeSubscription(runtime, 'idle');
    runtime.visible = null;
    emit(runtime);
    return;
  }
  const valid = requireTarget(target);
  const sub = runtime.subscription;
  if (sub !== null && sub.conversationId === valid.conversationId && sub.runId === valid.runId) {
    const run = runtime.runs.get(valid.conversationId);
    if (run !== undefined) refreshRunFacts(run, valid); // 同一 Run 幂等：不重复订阅
    runtime.visible = valid.conversationId;
    emit(runtime);
    return;
  }
  beginSubscription(runtime, valid);
}

/** 显式重开裁决：只有失败类恢复要求 + 非终态 Run 才重开（游标/认证各有专门动作）。 */
function explicitRecovery(runtime: RunEventsRuntime, kind: 'retry' | 'cursor'): void {
  const sub = runtime.subscription;
  const run = visibleRun(runtime);
  if (sub === null || run === null || sub.stream !== null) return;
  if (run.conversationId !== sub.conversationId || run.runId !== sub.runId) return;
  if (isTerminalRunStatus(run.status)) return;
  if (run.recovery === null || run.recovery.kind !== kind) return;
  run.recovery = null;
  if (kind === 'cursor') sub.recovery = true; // 本地游标已失效：从服务端安全快照重开
  reopen(runtime, sub);
}

function reset(runtime: RunEventsRuntime): void {
  closeSubscription(runtime, 'idle');
  runtime.visible = null;
  runtime.runs.clear();
  runtime.touched.clear();
  emit(runtime);
}

/** 构造页面级 Run 订阅控制器；一个页面一个实例（React 绑定见 `hooks/useRunEvents.ts`）。 */
export function createRunEventController(deps: RunEventControllerDeps): RunEventController {
  if (!Number.isInteger(deps.maxScopedRuns) || deps.maxScopedRuns < 1) {
    throw new RunStateError('RUN_SCOPE_LIMIT_INVALID');
  }
  const runtime: RunEventsRuntime = {
    deps,
    runs: new Map(),
    touched: new Map(),
    touchCounter: 0,
    visible: null,
    connection: idleConnection(),
    subscription: null,
    generation: 0,
    listeners: new Set(),
    cached: null
  };
  return {
    snapshot: () => currentSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    watch: (target) => watch(runtime, target),
    retry: () => explicitRecovery(runtime, 'retry'),
    recover: () => explicitRecovery(runtime, 'cursor'),
    close: () => closeSubscription(runtime, 'closed'),
    reset: () => reset(runtime),
    dispose() {
      reset(runtime);
      runtime.listeners.clear();
    }
  };
}
