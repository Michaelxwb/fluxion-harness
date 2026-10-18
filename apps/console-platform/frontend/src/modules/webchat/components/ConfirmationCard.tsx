/**
 * 原 Run 待确认卡片（design frontend §3.3 `ConfirmationCard`、§3.4 组件接口契约、§3.6 UI 状态确认、
 * §3.7 样式方案、§3.8 可访问性；TASK-083）。
 *
 * WAITING_INPUT 的**原 Run 确认交互**：完整展示服务端投影的候选（prompt/选项/有效期）与身份
 * （run/submission/source），用户显式选择候选后经 API-08 `resume` 提交——恢复请求只绑定服务端权威的
 * `submission_id`/`source_event_id`，`selection` 就是被选择的候选值逐字（不重新编号、不自行解释授权）。
 *
 * 1. **不自动消费**：`createConfirmationController` 创建与组件渲染都不发请求；`confirm` 是唯一提交入口
 *    （必须由用户显式选择候选），在途单飞（提交中不得重复提交），组件渲染只读 typed props。
 * 2. **只认服务端权威候选**：`confirmationResumeInput` 校验 selection 必须是 `confirmation.options` 里的
 *    真实值（否则 `CONFIRMATION_SELECTION_INVALID`，零 HTTP），submission/source 只从确认投影取；缺
 *    Run/消息号/已过期都在发请求前显式失败（服务端仍是权威，本地判定只做提前反馈）。
 * 3. **失败分型**：终态失败（过期/无效/已消费/无权限）不给重试动作；可重试失败（网络/忙碌）显式重试且
 *    **沿用同一身份**（同一 Run/submission/source/selection/message_id，不换号）。
 * 4. **忙碌不改投**：确认交互没有任何新建会话能力（端口只有 `resume`），本层不创建新会话、不自动重发；
 *    时间统一经 `DateTimeText`，文案只走 i18n 词条。
 *
 * 展示组件只收 typed props 与事件；不发 HTTP、不读 Cookie、不落持久存储、不自带定时器、不持有状态。
 */

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../../components/common/DateTimeText';
import { webchatErrorCode } from '../services/webchatApi';
import type {
  WebchatConfirmation,
  WebchatConfirmationOption,
  WebchatRequestOptions,
  WebchatResumeInput,
  WebchatResumeReceipt
} from '../types';

/** 本地稳定码：发请求前的显式失败（不用服务端 4xx 表达前端缺参）。 */
export type ConfirmationErrorCode =
  | 'CONFIRMATION_RUN_REQUIRED'
  | 'CONFIRMATION_SELECTION_INVALID'
  | 'CONFIRMATION_MESSAGE_ID_REQUIRED'
  | 'CONFIRMATION_EXPIRED';

/** 本地显式失败：稳定码 + 零 HTTP 副作用。 */
export class ConfirmationError extends Error {
  readonly code: ConfirmationErrorCode;

  constructor(code: ConfirmationErrorCode) {
    super(code);
    this.name = 'ConfirmationError';
    this.code = code;
  }
}

/** 稳定码 → 本地化词条键（本地码与服务端目录码同一份映射）。 */
export const CONFIRMATION_ERROR_KEYS: Record<string, string> = {
  CONFIRMATION_EXPIRED: 'chat.run.confirm.error.expired',
  CONFIRMATION_SELECTION_INVALID: 'chat.run.confirm.error.invalid',
  CONFIRMATION_MESSAGE_ID_REQUIRED: 'chat.run.confirm.error.failed',
  CONFIRMATION_RUN_REQUIRED: 'chat.run.confirm.error.failed',
  MEMORY_CONFIRMATION_EXPIRED: 'chat.run.confirm.error.expired',
  MEMORY_CONFIRMATION_INVALID: 'chat.run.confirm.error.invalid',
  MEMORY_CONFIRMATION_PENDING: 'chat.run.confirm.error.pending',
  RUN_BUSY: 'chat.run.confirm.error.busy',
  COMMON_NOT_FOUND: 'chat.run.confirm.error.unavailable',
  FORBIDDEN: 'chat.run.confirm.error.forbidden',
  UNAUTHORIZED: 'chat.run.confirm.error.forbidden',
  NETWORK_ERROR: 'chat.run.confirm.error.network',
  UNKNOWN: 'chat.run.confirm.error.failed'
};

export function confirmationErrorKey(code: string | null): string {
  if (code === null) return 'chat.run.confirm.error.failed';
  return CONFIRMATION_ERROR_KEYS[code] ?? 'chat.run.confirm.error.failed';
}

const FINAL_CONFIRMATION_FAILURES: ReadonlySet<string> = new Set<string>([
  'CONFIRMATION_EXPIRED',
  'CONFIRMATION_SELECTION_INVALID',
  'MEMORY_CONFIRMATION_EXPIRED',
  'MEMORY_CONFIRMATION_INVALID',
  'COMMON_NOT_FOUND',
  'FORBIDDEN',
  'UNAUTHORIZED'
]);

/** 终态失败（重试无意义）：过期/已消费/无效/无权限。 */
export function isConfirmationFailureFinal(code: string | null): boolean {
  return code !== null && FINAL_CONFIRMATION_FAILURES.has(code);
}

/** 本地过期判定：只按服务端 `expires_at`（无法解析时交由服务端裁决）。 */
export function confirmationExpired(expiresAt: string, nowMs: number): boolean {
  const deadline = Date.parse(expiresAt);
  return Number.isFinite(deadline) && deadline <= nowMs;
}

/** 恢复请求体：submission/source 只取服务端权威确认，selection 必须是真实候选值。 */
export function confirmationResumeInput(
  confirmation: WebchatConfirmation,
  input: { selection: string; messageId: string }
): WebchatResumeInput {
  if (!confirmation.options.some((option) => option.value === input.selection)) {
    throw new ConfirmationError('CONFIRMATION_SELECTION_INVALID');
  }
  const messageId = input.messageId.trim();
  if (messageId === '') throw new ConfirmationError('CONFIRMATION_MESSAGE_ID_REQUIRED');
  return {
    submission_id: confirmation.submission_id,
    source_event_id: confirmation.source_event_id,
    message_id: messageId,
    selection: input.selection
  };
}

export type ConfirmationPhase = 'IDLE' | 'SUBMITTING' | 'SUBMITTED' | 'FAILED';

export interface ConfirmationState {
  phase: ConfirmationPhase;
  runId: string | null;
  errorCode: string | null;
  receipt: WebchatResumeReceipt | null;
}

/** 恢复端口：`WebchatClient.resume`（API-08，显式绑定原 Run/submission/source）结构满足。 */
export interface ConfirmationResumePort {
  resume(
    runId: string,
    input: WebchatResumeInput,
    options?: WebchatRequestOptions
  ): Promise<WebchatResumeReceipt>;
}

export interface ConfirmationController {
  snapshot(): ConfirmationState;
  subscribe(listener: (snapshot: ConfirmationState) => void): () => void;
  /** 唯一提交入口：用户显式选择候选（不自动消费确认）。 */
  confirm(
    runId: string,
    confirmation: WebchatConfirmation,
    input: { selection: string; messageId: string }
  ): void;
  /** 可重试失败的显式重试：沿用同一身份（不换号、不改投）。 */
  retry(): void;
  reset(): void;
  dispose(): void;
}

interface ConfirmationAttempt {
  runId: string;
  /** 绑定后的恢复体（submission/source 来自服务端投影；重试原样重放）。 */
  input: WebchatResumeInput;
}

interface ConfirmationRuntime {
  port: ConfirmationResumePort;
  now: () => number;
  state: ConfirmationState;
  attempt: ConfirmationAttempt | null;
  generation: number;
  listeners: Set<(snapshot: ConfirmationState) => void>;
}

export interface ConfirmationControllerDeps {
  port: ConfirmationResumePort;
  /** 本地过期判定时钟（默认 `Date.now`；服务端仍是权威）。 */
  now?: () => number;
}

function idleConfirmationState(): ConfirmationState {
  return { phase: 'IDLE', runId: null, errorCode: null, receipt: null };
}

function sameConfirmationState(left: ConfirmationState, right: ConfirmationState): boolean {
  return (
    left.phase === right.phase &&
    left.runId === right.runId &&
    left.errorCode === right.errorCode &&
    left.receipt === right.receipt
  );
}

function publishConfirmation(runtime: ConfirmationRuntime, next: ConfirmationState): void {
  if (sameConfirmationState(runtime.state, next)) return;
  runtime.state = next;
  for (const listener of [...runtime.listeners]) listener(next);
}

/** 绑定校验结果：成功带请求体，失败带稳定码（零 HTTP）。 */
type BoundResumeInput =
  | { code: null; input: WebchatResumeInput }
  | { code: string; input: null };

function boundResumeInput(
  confirmation: WebchatConfirmation,
  input: { selection: string; messageId: string }
): BoundResumeInput {
  try {
    return { code: null, input: confirmationResumeInput(confirmation, input) };
  } catch (error) {
    return { code: error instanceof ConfirmationError ? error.code : 'UNKNOWN', input: null };
  }
}

/** 一次恢复派发：generation 围栏（重置/身份边界后的迟到结果不写状态）。 */
function dispatchResume(runtime: ConfirmationRuntime, attempt: ConfirmationAttempt, generation: number): void {
  runtime.port.resume(attempt.runId, attempt.input).then(
    (receipt) => {
      if (runtime.generation !== generation) return;
      publishConfirmation(runtime, {
        phase: 'SUBMITTED',
        runId: attempt.runId,
        errorCode: null,
        receipt
      });
    },
    (error: unknown) => {
      if (runtime.generation !== generation) return;
      publishConfirmation(runtime, {
        phase: 'FAILED',
        runId: attempt.runId,
        errorCode: webchatErrorCode(error),
        receipt: null
      });
    }
  );
}

function beginSubmit(runtime: ConfirmationRuntime, attempt: ConfirmationAttempt): void {
  runtime.attempt = attempt;
  runtime.generation += 1;
  const generation = runtime.generation;
  publishConfirmation(runtime, {
    phase: 'SUBMITTING',
    runId: attempt.runId,
    errorCode: null,
    receipt: null
  });
  dispatchResume(runtime, attempt, generation);
}

function failLocal(runtime: ConfirmationRuntime, runId: string | null, code: string): void {
  runtime.attempt = null;
  publishConfirmation(runtime, { phase: 'FAILED', runId, errorCode: code, receipt: null });
}

function confirm(
  runtime: ConfirmationRuntime,
  runId: string,
  confirmation: WebchatConfirmation,
  input: { selection: string; messageId: string }
): void {
  if (runtime.state.phase === 'SUBMITTING') return;
  const target = runId.trim();
  if (target === '') {
    failLocal(runtime, null, 'CONFIRMATION_RUN_REQUIRED');
    return;
  }
  if (confirmationExpired(confirmation.expires_at, runtime.now())) {
    failLocal(runtime, target, 'CONFIRMATION_EXPIRED');
    return;
  }
  const bound = boundResumeInput(confirmation, input);
  if (bound.code !== null) {
    failLocal(runtime, target, bound.code);
    return;
  }
  beginSubmit(runtime, { runId: target, input: bound.input });
}

function retry(runtime: ConfirmationRuntime): void {
  const { state, attempt } = runtime;
  if (state.phase !== 'FAILED' || attempt === null) return;
  if (isConfirmationFailureFinal(state.errorCode)) return;
  beginSubmit(runtime, attempt);
}

/** 构造确认状态机：提交单飞、重试不换号、失败分型（React 绑定由页面用 `subscribe` 完成）。 */
export function createConfirmationController(deps: ConfirmationControllerDeps): ConfirmationController {
  const runtime: ConfirmationRuntime = {
    port: deps.port,
    now: deps.now ?? (() => Date.now()),
    state: idleConfirmationState(),
    attempt: null,
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
    confirm: (runId, confirmation, input) => confirm(runtime, runId, confirmation, input),
    retry: () => retry(runtime),
    reset: () => {
      runtime.generation += 1;
      runtime.attempt = null;
      publishConfirmation(runtime, idleConfirmationState());
    },
    dispose() {
      runtime.generation += 1;
      runtime.attempt = null;
      runtime.state = idleConfirmationState();
      runtime.listeners.clear();
    }
  };
}

export interface ConfirmationCardProps {
  /** 原 Run（恢复请求的目标；页面据此绑定 `client.resume`）。 */
  runId: string;
  /** 服务端权威确认投影（完整候选 + 原 submission/source）。 */
  confirmation: WebchatConfirmation;
  /** 恢复尝试的固定消息号（调用方持有；重试沿用原值，不换号）。 */
  messageId: string;
  /** 提交在飞（防重复；选择动作不可用）。 */
  submitting: boolean;
  /** 提交失败（本地稳定码或服务端目录码）；进行中/成功为 null。 */
  errorCode: string | null;
  /** 本地过期判定参考时刻（服务端仍是权威）。 */
  nowMs: number;
  onConfirm(input: { selection: string; messageId: string }): void;
}

interface ConfirmationOptionListProps {
  options: readonly WebchatConfirmationOption[];
  locked: boolean;
  messageId: string;
  onConfirm(input: { selection: string; messageId: string }): void;
}

/** 候选列表：服务端顺序与文案原样（不重新编号），提交值就是候选值。 */
function ConfirmationOptionList(props: ConfirmationOptionListProps) {
  return (
    <ul className="chat-confirmation-options">
      {props.options.map((option) => (
        <li
          key={option.value}
          className="chat-confirmation-option"
          data-testid="chat-confirmation-option"
          data-value={option.value}
        >
          <Button
            size="small"
            className="chat-confirmation-choose"
            data-testid="chat-confirmation-choose"
            disabled={props.locked}
            onClick={() => props.onConfirm({ selection: option.value, messageId: props.messageId })}
          >
            {option.label}
          </Button>
        </li>
      ))}
    </ul>
  );
}

interface ConfirmationNoticesProps {
  expired: boolean;
  submitting: boolean;
  errorCode: string | null;
}

/** 提示区：过期、提交中与失败各自显式（过期时不再重复展示失败文案）。 */
function ConfirmationNotices(props: ConfirmationNoticesProps) {
  const { t } = useTranslation();
  return (
    <>
      {props.expired ? (
        <span className="chat-confirmation-expired" data-testid="chat-confirmation-expired">
          {t('chat.run.confirm.expired')}
        </span>
      ) : null}
      {props.submitting ? (
        <span className="chat-confirmation-submitting" data-testid="chat-confirmation-submitting">
          {t('chat.run.confirm.submitting')}
        </span>
      ) : null}
      {props.errorCode === null || props.expired ? null : (
        <span
          className="chat-confirmation-error"
          data-testid="chat-confirmation-error"
          data-error-code={props.errorCode}
        >
          {t(confirmationErrorKey(props.errorCode))}
        </span>
      )}
    </>
  );
}

/**
 * 待确认卡片：完整候选 + 身份 + 有效期；显式选择后由调用方经 `client.resume` 提交。
 *
 * 提交中/已过期/终态失败都禁用候选动作（防重复）；可重试失败仍可选择（同一消息号重放）。
 */
export function ConfirmationCard(props: ConfirmationCardProps) {
  const { t } = useTranslation();
  const { confirmation } = props;
  const expired = confirmationExpired(confirmation.expires_at, props.nowMs);
  const locked = props.submitting || expired || isConfirmationFailureFinal(props.errorCode);
  return (
    <section
      className="chat-confirmation-card"
      data-testid="chat-confirmation-card"
      data-run-id={props.runId}
      data-submission-id={confirmation.submission_id}
      data-source-event-id={confirmation.source_event_id}
      data-expired={expired ? 'true' : 'false'}
      aria-label={t('chat.run.confirm.label')}
    >
      <p className="chat-confirmation-prompt" data-testid="chat-confirmation-prompt">
        {confirmation.prompt}
      </p>
      <ConfirmationOptionList
        options={confirmation.options}
        locked={locked}
        messageId={props.messageId}
        onConfirm={props.onConfirm}
      />
      <p className="chat-confirmation-expires" data-testid="chat-confirmation-expires">
        {t('chat.run.confirm.expiresAt')}
        <DateTimeText value={confirmation.expires_at} />
      </p>
      <ConfirmationNotices
        expired={expired}
        submitting={props.submitting}
        errorCode={props.errorCode}
      />
    </section>
  );
}
