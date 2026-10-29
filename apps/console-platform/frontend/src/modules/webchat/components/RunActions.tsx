/**
 * 运行操作面（design frontend §3.3 `ExecutionStatus`/`ChatComposer` 停止、§3.4 `StreamingReply`、
 * §3.6 UI 状态、§3.7 样式方案、§3.8 可访问性；TASK-083）。
 *
 * 一个「可见 Run」的执行状态与操作：排队 / 模型等待 / 执行 / 等待工具 / 等待输入 / 终态与**连接状态
 * 分开**表达（不拿连接状态推断执行结果），排队原因与排队期限原样带出；停止必须**先确认**再调 API-08
 * `cancel`，且只取消这一个 Run。
 *
 * 1. **停止按精确来源**：`createRunStopController` 只接受当前可见 Run（`setVisibleRun`）的停止请求；
 *    确认前零请求（不产生任何 HTTP），确认后才 `port.cancel(runId)`（唯一出口）；在途重复点击单飞
 *    （一次请求）；切换可见 Run 递增 generation 围栏迟到结果——A 的取消结果不写成 B 的状态，也绝不为
 *    新 Run 复用旧确认（不用「用户 + Agent 取消最新 Run」的旧口径）。
 * 2. **停止中的状态机**：`IDLE → CONFIRMING → STOPPING → ACCEPTED/UNKNOWN/FAILED`；受理回执原样保存
 *    （`CANCELLING` = 停止中，终态 `CANCELLED` = 已停止）；结果未知（无确定响应）与显式失败分开，两者
 *    都可**重放同一 Run**（API-08 可重放，不产生第二次副作用）；终态 Run 不再提供停止动作。
 * 3. **恢复显式**：连接失败 / 游标过期只展示 TASK-082 `runState` 的显式动作（`retry()` / `recover()`），
 *    本层不自动重连、不自动补拉；认证失效只提示重新进入（没有重试动作）。
 * 4. **阶段只由持久事实派生**：`runActivityView` 从持久状态与持久执行阶段派生展示阶段
 *    （`WAITING_RUNTIME` ≠ `WAITING_MODEL` ≠ `RUNNING`），排队期限只在排队阶段展示（不显示名次或预计
 *    耗时），时间统一经 `DateTimeText`；本文件不持有定时器、不推算耗时。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器、不持有状态。
 */

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { isTerminalRunStatus, type RunConnectionSnapshot } from '../runState';
import { webchatErrorCode } from '../services/webchatApi';
import type {
  ExecutionStage,
  RunStatus,
  WebchatExecutionStatus,
  WebchatRequestOptions,
  WebchatRunCancelResult
} from '../types';

/** 展示阶段：排队与模型等待分开（design §3.6 发送/回复）。 */
export type RunActivityPhase =
  | 'queued'
  | 'waiting-model'
  | 'running'
  | 'waiting-tool'
  | 'waiting-input'
  | 'terminal';

/** 阶段词条：静态映射，词条键在源码里可被 i18n 检查直接看到。 */
export const RUN_ACTIVITY_PHASE_KEYS: Record<RunActivityPhase, string> = {
  queued: 'chat.run.phase.queued',
  'waiting-model': 'chat.run.phase.waitingModel',
  running: 'chat.run.phase.running',
  'waiting-tool': 'chat.run.phase.waitingTool',
  'waiting-input': 'chat.run.phase.waitingInput',
  terminal: 'chat.run.phase.terminal'
};

/** 连接状态词条：只描述订阅，不描述 Run 结果（TASK-082 `RunConnectionSnapshot`）。 */
export const RUN_CONNECTION_STATE_KEYS: Record<RunConnectionSnapshot['state'], string> = {
  idle: 'chat.run.connection.state.idle',
  connecting: 'chat.run.connection.state.connecting',
  open: 'chat.run.connection.state.open',
  reconnecting: 'chat.run.connection.state.reconnecting',
  closed: 'chat.run.connection.state.closed',
  failed: 'chat.run.connection.state.failed',
  'auth-required': 'chat.run.connection.state.authRequired',
  'cursor-recovery': 'chat.run.connection.state.cursorRecovery'
};

/** 展示投影：阶段 + 排队原因/期限与等待/执行计时（全部原样带出）。 */
export interface RunActivityView {
  phase: RunActivityPhase;
  reason: string | null;
  queuedAt: string | null;
  queueDeadlineAt: string | null;
  startedAt: string | null;
}

const TERMINAL_STAGES: ReadonlySet<ExecutionStage> = new Set<ExecutionStage>([
  'COMPLETED',
  'FAILED',
  'CANCELLED'
]);
const QUEUE_PHASES: ReadonlySet<RunActivityPhase> = new Set<RunActivityPhase>(['queued', 'waiting-model']);

/** 展示阶段：终态 > 等待输入 > 等待工具 > 模型等待 > 执行 > 排队（只由持久事实派生）。 */
export function runActivityPhase(
  status: RunStatus,
  executionStatus: WebchatExecutionStatus | null
): RunActivityPhase {
  const stage = executionStatus === null ? null : executionStatus.stage;
  if (isTerminalRunStatus(status) || (stage !== null && TERMINAL_STAGES.has(stage))) return 'terminal';
  if (stage === 'WAITING_INPUT' || status === 'WAITING_INPUT') return 'waiting-input';
  if (stage === 'WAITING_TOOL' || status === 'WAITING_TOOL') return 'waiting-tool';
  if (stage === 'WAITING_MODEL') return 'waiting-model';
  if (stage === 'RUNNING' || status === 'RUNNING') return 'running';
  return 'queued';
}

/** 活动投影：排队期限只在排队阶段展示（已开始执行的过期期限不再展示）。 */
export function runActivityView(
  status: RunStatus,
  executionStatus: WebchatExecutionStatus | null
): RunActivityView {
  const phase = runActivityPhase(status, executionStatus);
  return {
    phase,
    reason: executionStatus === null ? null : executionStatus.reason,
    queuedAt: executionStatus === null ? null : executionStatus.queued_at,
    queueDeadlineAt:
      executionStatus !== null && QUEUE_PHASES.has(phase) ? executionStatus.deadline_at : null,
    startedAt: executionStatus === null ? null : executionStatus.started_at
  };
}

/** 停止状态机的显式阶段（`UNKNOWN` = 无确定响应，重放安全）。 */
export type RunStopPhase = 'IDLE' | 'CONFIRMING' | 'STOPPING' | 'ACCEPTED' | 'UNKNOWN' | 'FAILED';

/** 停止状态：绑定精确 Run 与受理回执（`receipt.status` 是 Runtime 显示状态）。 */
export interface RunStopState {
  phase: RunStopPhase;
  runId: string | null;
  errorCode: string | null;
  receipt: WebchatRunCancelResult | null;
}

/** 展示裁决：终态不再提供停止动作，受理未落地显示停止中。 */
export type RunStopView =
  | { kind: 'idle' }
  | { kind: 'confirming' }
  | { kind: 'stopping' }
  | { kind: 'stopped' }
  | { kind: 'unknown'; errorCode: string | null }
  | { kind: 'failed'; errorCode: string | null }
  | { kind: 'unavailable' };

/** 停止失败词条：稳定码 → 本地化文案（未登记回落显式失败文案）。 */
export const RUN_STOP_ERROR_KEYS: Record<string, string> = {
  COMMON_NOT_FOUND: 'chat.run.stop.error.notFound',
  FORBIDDEN: 'chat.run.stop.error.forbidden',
  RUN_BUSY: 'chat.run.stop.error.busy',
  NETWORK_ERROR: 'chat.run.stop.error.network',
  UNKNOWN: 'chat.run.stop.error.failed'
};

export function runStopErrorKey(code: string | null): string {
  if (code === null) return 'chat.run.stop.error.failed';
  return RUN_STOP_ERROR_KEYS[code] ?? 'chat.run.stop.error.failed';
}

/** 展示裁决：终态 Run 不再提供停止动作（已取消的如实展示结果）。 */
export function runStopView(status: RunStatus, stop: RunStopState): RunStopView {
  if (stop.phase === 'CONFIRMING') {
    return isTerminalRunStatus(status) ? { kind: 'unavailable' } : { kind: 'confirming' };
  }
  if (stop.phase === 'STOPPING') return { kind: 'stopping' };
  if (stop.phase === 'FAILED') return { kind: 'failed', errorCode: stop.errorCode };
  if (stop.phase === 'UNKNOWN') return { kind: 'unknown', errorCode: stop.errorCode };
  if (stop.phase === 'ACCEPTED') {
    if (status === 'CANCELLED') return { kind: 'stopped' };
    return isTerminalRunStatus(status) ? { kind: 'unavailable' } : { kind: 'stopping' };
  }
  if (isTerminalRunStatus(status)) return status === 'CANCELLED' ? { kind: 'stopped' } : { kind: 'unavailable' };
  return { kind: 'idle' };
}

/** 取消端口：`WebchatClient.cancel`（API-08 精确取消，可重放）结构满足。 */
export interface RunCancelPort {
  cancel(runId: string, options?: WebchatRequestOptions): Promise<WebchatRunCancelResult>;
}

export interface RunStopController {
  snapshot(): RunStopState;
  subscribe(listener: (snapshot: RunStopState) => void): () => void;
  /** 请求停止：只进入待确认（零请求），绑定当前可见 Run。 */
  requestStop(runId: string): void;
  /** 确认停止：按绑定的精确 Run 调 API-08（唯一发请求入口）。 */
  confirmStop(): void;
  dismissStop(): void;
  /** 重放同一 Run 的取消（失败/结果未知后的显式动作）。 */
  retryStop(): void;
  /** 可见 Run 变化：绑定不同 Run 时复位并围栏迟到结果。 */
  setVisibleRun(runId: string | null): void;
  reset(): void;
  dispose(): void;
}

interface RunStopRuntime {
  port: RunCancelPort;
  state: RunStopState;
  visibleRunId: string | null;
  generation: number;
  listeners: Set<(snapshot: RunStopState) => void>;
}

function idleStopState(): RunStopState {
  return { phase: 'IDLE', runId: null, errorCode: null, receipt: null };
}

function sameStopState(left: RunStopState, right: RunStopState): boolean {
  return (
    left.phase === right.phase &&
    left.runId === right.runId &&
    left.errorCode === right.errorCode &&
    left.receipt === right.receipt
  );
}

function publishStop(runtime: RunStopRuntime, next: RunStopState): void {
  if (sameStopState(runtime.state, next)) return;
  runtime.state = next;
  for (const listener of [...runtime.listeners]) listener(next);
}

/** 有确定响应（含服务端错误体）才是显式失败；无响应一律按结果未知处理。 */
function hasResponse(error: unknown): boolean {
  const response = (error as { response?: unknown } | null)?.response;
  return response !== undefined && response !== null;
}

/** 一次取消派发：generation 与绑定 Run 双重围栏，迟到结果不写新状态。 */
function dispatchCancel(runtime: RunStopRuntime, runId: string, generation: number): void {
  runtime.port.cancel(runId).then(
    (receipt) => {
      if (runtime.generation !== generation || runtime.state.runId !== runId) return;
      publishStop(runtime, { phase: 'ACCEPTED', runId, errorCode: null, receipt });
    },
    (error: unknown) => {
      if (runtime.generation !== generation || runtime.state.runId !== runId) return;
      publishStop(runtime, {
        phase: hasResponse(error) ? 'FAILED' : 'UNKNOWN',
        runId,
        errorCode: webchatErrorCode(error),
        receipt: null
      });
    }
  );
}

function beginCancel(runtime: RunStopRuntime, runId: string): void {
  runtime.generation += 1;
  const generation = runtime.generation;
  publishStop(runtime, { phase: 'STOPPING', runId, errorCode: null, receipt: null });
  dispatchCancel(runtime, runId, generation);
}

function requestStop(runtime: RunStopRuntime, runId: string): void {
  const target = runId.trim();
  if (target === '' || target !== runtime.visibleRunId) return;
  if (runtime.state.phase !== 'IDLE') return;
  publishStop(runtime, { phase: 'CONFIRMING', runId: target, errorCode: null, receipt: null });
}

function confirmStop(runtime: RunStopRuntime): void {
  const state = runtime.state;
  if (state.phase !== 'CONFIRMING' || state.runId === null) return;
  if (state.runId !== runtime.visibleRunId) {
    publishStop(runtime, idleStopState());
    return;
  }
  beginCancel(runtime, state.runId);
}

function retryStop(runtime: RunStopRuntime): void {
  const state = runtime.state;
  if (state.phase !== 'FAILED' && state.phase !== 'UNKNOWN') return;
  if (state.runId === null || state.runId !== runtime.visibleRunId) {
    publishStop(runtime, idleStopState());
    return;
  }
  beginCancel(runtime, state.runId);
}

function setVisibleRun(runtime: RunStopRuntime, runId: string | null): void {
  const next = runId === null || runId.trim() === '' ? null : runId.trim();
  runtime.visibleRunId = next;
  if (runtime.state.runId !== null && runtime.state.runId !== next) {
    runtime.generation += 1;
    publishStop(runtime, idleStopState());
  }
}

/** 构造停止状态机：一个可见 Run 一个实例（React 绑定由页面用 `subscribe` 完成）。 */
export function createRunStopController(port: RunCancelPort): RunStopController {
  const runtime: RunStopRuntime = {
    port,
    state: idleStopState(),
    visibleRunId: null,
    generation: 0,
    listeners: new Set()
  };
  return {
    snapshot: () => runtime.state,
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    requestStop: (runId) => requestStop(runtime, runId),
    confirmStop: () => confirmStop(runtime),
    dismissStop: () => {
      if (runtime.state.phase === 'CONFIRMING') publishStop(runtime, idleStopState());
    },
    retryStop: () => retryStop(runtime),
    setVisibleRun: (runId) => setVisibleRun(runtime, runId),
    reset: () => {
      runtime.generation += 1;
      runtime.visibleRunId = null;
      publishStop(runtime, idleStopState());
    },
    dispose() {
      runtime.generation += 1;
      runtime.visibleRunId = null;
      runtime.state = idleStopState();
      runtime.listeners.clear();
    }
  };
}

interface RunStopRegionProps {
  view: RunStopView;
  onRequestStop(): void;
  onConfirmStop(): void;
  onDismissStop(): void;
  onRetryStop(): void;
}

interface StopConfirmActionsProps {
  onConfirmStop(): void;
  onDismissStop(): void;
}

/** 待确认动作：显式确认或取消（取消不发任何请求）。 */
function StopConfirmActions(props: StopConfirmActionsProps) {
  const { t } = useTranslation();
  return (
    <>
      <span className="chat-run-stop-confirm-prompt" data-testid="chat-run-stop-confirm-prompt">
        {t('chat.run.stop.confirmPrompt')}
      </span>
      <Button
        size="small"
        className="chat-run-stop-confirm"
        data-testid="chat-run-stop-confirm"
        onClick={props.onConfirmStop}
      >
        {t('chat.run.stop.confirm')}
      </Button>
      <Button
        size="small"
        theme="borderless"
        className="chat-run-stop-dismiss"
        data-testid="chat-run-stop-dismiss"
        onClick={props.onDismissStop}
      >
        {t('common.cancel')}
      </Button>
    </>
  );
}

interface StopRetryActionsProps {
  kind: 'unknown' | 'failed';
  errorCode: string | null;
  onRetryStop(): void;
}

/** 失败/结果未知：本地化反馈 + 显式重试（重放同一 Run，不产生第二次副作用）。 */
function StopRetryActions(props: StopRetryActionsProps) {
  const { t } = useTranslation();
  const unknown = props.kind === 'unknown';
  return (
    <>
      <span
        className={unknown ? 'chat-run-stop-unknown' : 'chat-run-stop-error'}
        data-testid={unknown ? 'chat-run-stop-unknown' : 'chat-run-stop-error'}
        data-error-code={unknown ? undefined : (props.errorCode ?? undefined)}
      >
        {unknown ? t('chat.run.stop.unknown') : t(runStopErrorKey(props.errorCode))}
      </span>
      <Button size="small" data-testid="chat-run-stop-retry" onClick={props.onRetryStop}>
        {t('common.retry')}
      </Button>
    </>
  );
}

/** 停止区域：待确认 / 停止中 / 已停止 / 失败 / 结果未知各自显式（终态不占位）。 */
function RunStopRegion(props: RunStopRegionProps) {
  const { t } = useTranslation();
  const { view } = props;
  if (view.kind === 'unavailable') return null;
  return (
    <div
      className="chat-run-stop"
      data-testid="chat-run-stop"
      data-stop-view={view.kind}
      role="group"
      aria-label={t('chat.run.stop.label')}
    >
      {view.kind === 'idle' ? (
        <Button
          size="small"
          className="chat-run-stop-action"
          data-testid="chat-run-stop-action"
          onClick={props.onRequestStop}
        >
          {t('chat.run.stop.action')}
        </Button>
      ) : null}
      {view.kind === 'confirming' ? (
        <StopConfirmActions onConfirmStop={props.onConfirmStop} onDismissStop={props.onDismissStop} />
      ) : null}
      {view.kind === 'stopping' ? (
        <span className="chat-run-stop-stopping" data-testid="chat-run-stop-stopping">
          {t('chat.run.stop.stopping')}
        </span>
      ) : null}
      {view.kind === 'stopped' ? (
        <span className="chat-run-stop-stopped" data-testid="chat-run-stop-stopped">
          {t('chat.run.stop.stopped')}
        </span>
      ) : null}
      {view.kind === 'unknown' ? (
        <StopRetryActions kind="unknown" errorCode={view.errorCode} onRetryStop={props.onRetryStop} />
      ) : null}
      {view.kind === 'failed' ? (
        <StopRetryActions kind="failed" errorCode={view.errorCode} onRetryStop={props.onRetryStop} />
      ) : null}
    </div>
  );
}

interface RunActivityRegionProps {
  activity: RunActivityView;
}

/** 活动区域：阶段 + 排队原因/期限 + 等待/执行计时（期限只在排队阶段出现）。 */
function RunActivityRegion(props: RunActivityRegionProps) {
  const { t } = useTranslation();
  const { activity } = props;
  return (
    <div className="chat-run-activity" data-testid="chat-run-activity" data-phase={activity.phase}>
      <span className="chat-run-phase" data-testid="chat-run-phase">
        {t(RUN_ACTIVITY_PHASE_KEYS[activity.phase])}
      </span>
      {activity.reason === null ? null : (
        <span className="chat-run-queue-reason" data-testid="chat-run-queue-reason">
          {t('chat.run.queue.reason', { reason: activity.reason })}
        </span>
      )}
      {activity.queueDeadlineAt === null ? null : (
        <span className="chat-run-queue-deadline" data-testid="chat-run-queue-deadline">
          {t('chat.run.queue.deadline')}
          <DateTimeText value={activity.queueDeadlineAt} />
        </span>
      )}
      {activity.queuedAt === null ? null : (
        <span className="chat-run-queued-at" data-testid="chat-run-queued-at">
          {t('chat.run.queuedAt')}
          <DateTimeText value={activity.queuedAt} />
        </span>
      )}
      {activity.startedAt === null ? null : (
        <span className="chat-run-started-at" data-testid="chat-run-started-at">
          {t('chat.run.startedAt')}
          <DateTimeText value={activity.startedAt} />
        </span>
      )}
    </div>
  );
}

interface RunConnectionRegionProps {
  connection: RunConnectionSnapshot;
  onRetry(): void;
  onRecover(): void;
}

/** 连接区域：只描述订阅状态；失败/游标过期给出显式动作（不自动重连），认证失效只提示。 */
function RunConnectionRegion(props: RunConnectionRegionProps) {
  const { t } = useTranslation();
  const { connection } = props;
  return (
    <div
      className="chat-run-connection"
      data-testid="chat-run-connection"
      data-connection-state={connection.state}
      data-cursor-after={connection.cursor?.after_seq ?? undefined}
      data-cursor-earliest={connection.cursor?.earliest_seq ?? undefined}
      data-cursor-through={connection.cursor?.through_seq ?? undefined}
      role="group"
      aria-label={t('chat.run.connection.label')}
    >
      <span className="chat-run-connection-state" data-testid="chat-run-connection-state">
        {t(RUN_CONNECTION_STATE_KEYS[connection.state])}
      </span>
      {connection.retries > 0 ? (
        <span className="chat-run-connection-retries" data-testid="chat-run-connection-retries">
          {t('chat.run.connection.retries', { count: connection.retries })}
        </span>
      ) : null}
      {connection.state === 'failed' ? (
        <Button
          size="small"
          className="chat-run-connection-retry"
          data-testid="chat-run-connection-retry"
          onClick={props.onRetry}
        >
          {t('common.retry')}
        </Button>
      ) : null}
      {connection.state === 'cursor-recovery' ? (
        <Button
          size="small"
          className="chat-run-connection-recover"
          data-testid="chat-run-connection-recover"
          onClick={props.onRecover}
        >
          {t('chat.run.connection.recover')}
        </Button>
      ) : null}
      {connection.state === 'auth-required' ? (
        <span className="chat-run-connection-auth" data-testid="chat-run-connection-auth">
          {t('chat.run.connection.authNotice')}
        </span>
      ) : null}
    </div>
  );
}

export interface RunActionsProps {
  /** 可见 Run 的持久状态（终态不再提供停止动作）。 */
  status: RunStatus;
  /** 持久执行阶段（API-15/受理回执原样带出）；未就绪为 null。 */
  executionStatus: WebchatExecutionStatus | null;
  /** 连接状态（TASK-082 快照；与执行状态分开表达）。 */
  connection: RunConnectionSnapshot;
  /** 停止状态机快照（`createRunStopController`）。 */
  stop: RunStopState;
  onRequestStop(): void;
  onConfirmStop(): void;
  onDismissStop(): void;
  onRetryStop(): void;
  /** 显式重连（TASK-082 `retry()`；只在失败态可用）。 */
  onRetry(): void;
  /** 显式游标恢复（TASK-082 `recover()`；只在游标过期态可用）。 */
  onRecover(): void;
}

/**
 * 运行操作面：执行阶段 + 排队事实 + 连接状态 + 停止交互。
 *
 * 展示组件只吃 typed props 与事件：状态机（`createRunStopController`）与恢复动作（TASK-082）由页面
 * 持有，这里只渲染真实事实与显式动作。
 */
export function RunActions(props: RunActionsProps) {
  const { t } = useTranslation();
  return (
    <section
      className="chat-run-actions"
      data-testid="chat-run-actions"
      data-run-status={props.status}
      data-execution-stage={props.executionStatus?.stage ?? undefined}
      aria-label={t('chat.run.label')}
    >
      <RunActivityRegion activity={runActivityView(props.status, props.executionStatus)} />
      <RunConnectionRegion
        connection={props.connection}
        onRetry={props.onRetry}
        onRecover={props.onRecover}
      />
      <RunStopRegion
        view={runStopView(props.status, props.stop)}
        onRequestStop={props.onRequestStop}
        onConfirmStop={props.onConfirmStop}
        onDismissStop={props.onDismissStop}
        onRetryStop={props.onRetryStop}
      />
    </section>
  );
}
