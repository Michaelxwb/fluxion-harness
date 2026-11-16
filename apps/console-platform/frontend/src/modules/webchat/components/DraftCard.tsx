/**
 * 定时任务 Draft 确认卡片（design frontend §3.3 `DraftCard`、§3.4、§3.6；TASK-026）。
 *
 * 卡片事实来自 canonical `schedule.draft.ready` 事件（当前流）或同一条持久事件的历史投影：
 * 做什么、何时（`when` 文本自带 IANA 时区）、首次触发、结果去哪看、依赖（AGENTIC 即工具
 * allowlist）、预算、AGENTIC 的只读记忆约束，以及 UPDATE 的前后差异。
 *
 * **不是 Run 的确认通道**：`confirmation.required`/resume 确认的是「这个 Run 是否继续」，
 * 这张卡片确认的是「这条配置是否生效」，按钮只调用注入端口 `confirmDraft`（API-31 →
 * API-27）。本模块结构上没有任何 Run resume 能力。
 *
 * 1. **不自动提交**：渲染与 `createDraftConfirmController` 构造都不发请求；`confirm` 是唯一
 *    提交入口，必须由用户显式点击。
 * 2. **在途单飞与幂等重放**：同一 Draft 提交中不得重复提交；可重试失败后的显式重试沿用
 *    首次的 `Idempotency-Key` 与请求体（一次用户动作 = 一个稳定引用）。
 * 3. **失败分型**：过期/冲突/配额/无权限是明确文案；过期在本地先以服务端 `expires_at` 判定
 *    （服务端仍是权威），终态失败不再提供重试。
 * 4. **展示组件无状态**：不自带定时器、不读 Cookie/持久存储、文案只走 i18n 词条，时间统一经
 *    `DateTimeText`。
 */

import { Button } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../../api/client';
import { DateTimeText } from '../../../components/common/DateTimeText';
import { taskCenterErrorCode } from '../services/taskCenterApi';
import type {
  ScheduleDiffEntry,
  ScheduleDraftConfirmInput,
  ScheduleDraftConfirmReceipt,
  ScheduleDraftStatus,
  ScheduleDraftStatusResponse,
  ScheduleMode,
  SchedulePreview,
  WebchatScheduleDraftReady,
  WebchatWriteOptions
} from '../types';

/** 稳定码 → 本地化词条键（本地缺参码与服务端目录码同一份映射）。 */
export const DRAFT_ERROR_KEYS: Record<string, string> = {
  SCHEDULE_DRAFT_EXPIRED: 'chat.draft.error.expired',
  SCHEDULE_CONFIRMATION_REQUIRED: 'chat.draft.error.conflict',
  SCHEDULE_VERSION_CONFLICT: 'chat.draft.error.conflict',
  SCHEDULE_STATE_INVALID: 'chat.draft.error.conflict',
  IDEMPOTENCY_MISMATCH: 'chat.draft.error.conflict',
  SCHEDULE_QUOTA_EXCEEDED: 'chat.draft.error.quota',
  SCHEDULE_DEPENDENCY_MISSING: 'chat.draft.error.dependency',
  SCHEDULE_ADMISSION_FAILED: 'chat.draft.error.admission',
  SCHEDULE_NOT_FOUND: 'chat.draft.error.unavailable',
  COMMON_NOT_FOUND: 'chat.draft.error.unavailable',
  FORBIDDEN: 'chat.draft.error.forbidden',
  UNAUTHORIZED: 'chat.draft.error.forbidden',
  NETWORK_ERROR: 'chat.draft.error.network',
  DRAFT_ID_REQUIRED: 'chat.draft.error.failed',
  DRAFT_HASH_REQUIRED: 'chat.draft.error.failed',
  IDEMPOTENCY_KEY_REQUIRED: 'chat.draft.error.failed',
  UNKNOWN: 'chat.draft.error.failed'
};

export function draftErrorKey(code: string | null): string {
  if (code === null) return 'chat.draft.error.failed';
  return DRAFT_ERROR_KEYS[code] ?? 'chat.draft.error.failed';
}

/** 终态失败（重试无意义）：过期/已确认/无权限/不存在。 */
const FINAL_DRAFT_FAILURES: ReadonlySet<string> = new Set<string>([
  'SCHEDULE_DRAFT_EXPIRED',
  'SCHEDULE_CONFIRMATION_REQUIRED',
  'SCHEDULE_NOT_FOUND',
  'COMMON_NOT_FOUND',
  'FORBIDDEN',
  'UNAUTHORIZED'
]);

export function isDraftFailureFinal(code: string | null): boolean {
  return code !== null && FINAL_DRAFT_FAILURES.has(code);
}

/** 本地过期判定：只按服务端 `expires_at`（无法解析时交由服务端裁决）。 */
export function draftExpired(expiresAt: string, nowMs: number): boolean {
  const deadline = Date.parse(expiresAt);
  return Number.isFinite(deadline) && deadline <= nowMs;
}

/** 确认请求体：只绑定卡片上的 `draft_hash`（哈希不一致由服务端显式拒绝）。 */
export function draftConfirmInput(draft: WebchatScheduleDraftReady): ScheduleDraftConfirmInput {
  const hash = draft.draft_hash.trim();
  if (hash === '') throw new Error('DRAFT_HASH_REQUIRED');
  return { draft_hash: hash };
}

export type DraftConfirmPhase = 'IDLE' | 'CHECKING' | 'SUBMITTING' | 'SUBMITTED' | 'FAILED';

export interface DraftConfirmEntry {
  phase: DraftConfirmPhase;
  errorCode: string | null;
  receipt: ScheduleDraftConfirmReceipt | null;
}

export const IDLE_DRAFT_CONFIRM: DraftConfirmEntry = {
  phase: 'IDLE',
  errorCode: null,
  receipt: null
};

export interface DraftConfirmState {
  /** 只保存非 IDLE 的 Draft 状态；未出现过的 Draft 读取为 `IDLE_DRAFT_CONFIRM`。 */
  entries: Readonly<Record<string, DraftConfirmEntry>>;
}

/** 确认端口：任务中心 typed client 的 `confirmDraft`（结构上没有任何 Run resume 能力）。 */
export interface DraftConfirmPort {
  confirmDraft(
    draftId: string,
    input: ScheduleDraftConfirmInput,
    options: WebchatWriteOptions
  ): Promise<ScheduleDraftConfirmReceipt>;
  /** 历史卡片刷新后的确认状态；缺省时不做自动状态校验（只用于无法接线的测试）。 */
  getDraftStatus?(draftId: string): Promise<ScheduleDraftStatusResponse>;
}

export interface DraftConfirmController {
  snapshot(): DraftConfirmState;
  subscribe(listener: (snapshot: DraftConfirmState) => void): () => void;
  entry(draftId: string): DraftConfirmEntry;
  /** 历史卡片进入页面时读取一次服务端确认状态（幂等，不重放用户动作）。 */
  resolve(draft: WebchatScheduleDraftReady): void;
  /** 唯一提交入口：用户显式确认（不自动消费）。 */
  confirm(draft: WebchatScheduleDraftReady): void;
  /** 可重试失败的显式重试：确认失败沿用同一幂等键；状态读取失败则重读状态。 */
  retry(draftId: string): void;
  reset(): void;
  dispose(): void;
}

interface DraftAttempt {
  draft: WebchatScheduleDraftReady;
  idempotencyKey: string;
}

export interface DraftConfirmControllerDeps {
  port: DraftConfirmPort;
  /** 本地过期判定时钟（默认 `Date.now`；服务端仍是权威）。 */
  now?: () => number;
  /** 幂等键生成器（默认共享 `newRequestId`）；一次用户动作只生成一次。 */
  newKey?: () => string;
}

interface DraftConfirmRuntime {
  port: DraftConfirmPort;
  now: () => number;
  newKey: () => string;
  entries: Map<string, DraftConfirmEntry>;
  attempts: Map<string, DraftAttempt>;
  /** 已自动读取过状态的 Draft：页面重渲染/历史重放不重复发请求。 */
  hydrated: Set<string>;
  /** 状态读取过的 Draft：读取失败后的「重试」重读状态，而不是盲目确认。 */
  statusChecks: Map<string, WebchatScheduleDraftReady>;
  generation: number;
  listeners: Set<(snapshot: DraftConfirmState) => void>;
  cached: DraftConfirmState | null;
}

function publish(runtime: DraftConfirmRuntime): void {
  runtime.cached = null;
  const snapshot = runtimeSnapshot(runtime);
  for (const listener of [...runtime.listeners]) listener(snapshot);
}

function sameEntry(left: DraftConfirmEntry, right: DraftConfirmEntry): boolean {
  return (
    left.phase === right.phase &&
    left.errorCode === right.errorCode &&
    left.receipt === right.receipt
  );
}

function setEntry(runtime: DraftConfirmRuntime, draftId: string, entry: DraftConfirmEntry): void {
  const previous = runtime.entries.get(draftId);
  if (previous !== undefined && sameEntry(previous, entry)) return;
  runtime.entries.set(draftId, entry);
  publish(runtime);
}

function runtimeSnapshot(runtime: DraftConfirmRuntime): DraftConfirmState {
  if (runtime.cached !== null) return runtime.cached;
  const entries: Record<string, DraftConfirmEntry> = {};
  for (const [draftId, entry] of runtime.entries) entries[draftId] = entry;
  runtime.cached = { entries };
  return runtime.cached;
}

function entryOf(runtime: DraftConfirmRuntime, draftId: string): DraftConfirmEntry {
  return runtime.entries.get(draftId) ?? IDLE_DRAFT_CONFIRM;
}

function dispatch(
  runtime: DraftConfirmRuntime,
  attempt: DraftAttempt,
  generation: number
): void {
  const draftId = attempt.draft.draft_id;
  setEntry(runtime, draftId, { phase: 'SUBMITTING', errorCode: null, receipt: null });
  runtime.port
    .confirmDraft(draftId, draftConfirmInput(attempt.draft), {
      idempotencyKey: attempt.idempotencyKey
    })
    .then(
      (receipt) => {
        if (runtime.generation !== generation) return;
        setEntry(runtime, draftId, { phase: 'SUBMITTED', errorCode: null, receipt });
      },
      (error: unknown) => {
        if (runtime.generation !== generation) return;
        setEntry(runtime, draftId, {
          phase: 'FAILED',
          errorCode: taskCenterErrorCode(error),
          receipt: null
        });
      }
    );
}

/** 服务端状态 → 卡片状态：CONFIRMED 卡片只读；PENDING 仍可确认；终态失败不可重试。 */
export function draftEntryForStatus(status: ScheduleDraftStatus): DraftConfirmEntry {
  switch (status) {
    case 'CONFIRMED':
      return { phase: 'SUBMITTED', errorCode: null, receipt: null };
    case 'PENDING':
      return IDLE_DRAFT_CONFIRM;
    case 'EXPIRED':
      return { phase: 'FAILED', errorCode: 'SCHEDULE_DRAFT_EXPIRED', receipt: null };
    case 'CANCELLED':
    case 'REJECTED':
      return { phase: 'FAILED', errorCode: 'SCHEDULE_STATE_INVALID', receipt: null };
    default:
      return { phase: 'FAILED', errorCode: 'UNKNOWN', receipt: null };
  }
}

function checkStatus(
  runtime: DraftConfirmRuntime,
  draft: WebchatScheduleDraftReady,
  generation: number
): void {
  const draftId = draft.draft_id.trim();
  if (draftId === '' || runtime.port.getDraftStatus === undefined) return;
  setEntry(runtime, draftId, { phase: 'CHECKING', errorCode: null, receipt: null });
  runtime.port.getDraftStatus(draftId).then(
    (response) => {
      if (runtime.generation !== generation) return;
      if (entryOf(runtime, draftId).phase !== 'CHECKING') return;
      setEntry(runtime, draftId, draftEntryForStatus(response.status));
    },
    (error: unknown) => {
      if (runtime.generation !== generation) return;
      if (entryOf(runtime, draftId).phase !== 'CHECKING') return;
      setEntry(runtime, draftId, {
        phase: 'FAILED',
        errorCode: taskCenterErrorCode(error),
        receipt: null
      });
    }
  );
}

function resolveDraft(runtime: DraftConfirmRuntime, draft: WebchatScheduleDraftReady): void {
  const draftId = draft.draft_id.trim();
  if (draftId === '') return;
  if (runtime.hydrated.has(draftId) || runtime.port.getDraftStatus === undefined) return;
  if (entryOf(runtime, draftId).phase !== 'IDLE') return;
  runtime.hydrated.add(draftId);
  runtime.statusChecks.set(draftId, draft);
  // 不推进全局 generation：状态读取与其它 Draft 的确认响应互不相干。
  checkStatus(runtime, draft, runtime.generation);
}

function confirm(runtime: DraftConfirmRuntime, draft: WebchatScheduleDraftReady): void {
  const draftId = draft.draft_id.trim();
  if (draftId === '') {
    // 没有身份就不构造“无归属”的确认；错误落在显式失败态而不是静默。
    return;
  }
  const current = entryOf(runtime, draftId);
  if (
    current.phase === 'CHECKING' ||
    current.phase === 'SUBMITTING' ||
    current.phase === 'SUBMITTED'
  ) {
    return;
  }
  if (current.phase === 'FAILED' && isDraftFailureFinal(current.errorCode)) return;
  if (draftExpired(draft.expires_at, runtime.now())) {
    setEntry(runtime, draftId, {
      phase: 'FAILED',
      errorCode: 'SCHEDULE_DRAFT_EXPIRED',
      receipt: null
    });
    return;
  }
  const existing = runtime.attempts.get(draftId);
  const attempt =
    existing !== undefined && existing.draft.draft_hash === draft.draft_hash
      ? existing
      : { draft, idempotencyKey: runtime.newKey() };
  runtime.attempts.set(draftId, attempt);
  runtime.generation += 1;
  dispatch(runtime, attempt, runtime.generation);
}

function retryDraft(runtime: DraftConfirmRuntime, draftId: string): void {
  const current = entryOf(runtime, draftId);
  if (current.phase !== 'FAILED' || isDraftFailureFinal(current.errorCode)) return;
  const attempt = runtime.attempts.get(draftId);
  if (attempt === undefined) {
    // 失败发生在状态读取而不是确认：重读状态，绝不把「重试」变成一次确认。
    const draft = runtime.statusChecks.get(draftId);
    if (draft !== undefined) checkStatus(runtime, draft, runtime.generation);
    return;
  }
  if (draftExpired(attempt.draft.expires_at, runtime.now())) {
    setEntry(runtime, draftId, {
      phase: 'FAILED',
      errorCode: 'SCHEDULE_DRAFT_EXPIRED',
      receipt: null
    });
    return;
  }
  runtime.generation += 1;
  dispatch(runtime, attempt, runtime.generation);
}

/** 构造 Draft 确认状态机：在途单飞、重试不换号、失败分型（React 绑定由页面完成）。 */
export function createDraftConfirmController(
  deps: DraftConfirmControllerDeps
): DraftConfirmController {
  const runtime: DraftConfirmRuntime = {
    port: deps.port,
    now: deps.now ?? (() => Date.now()),
    newKey: deps.newKey ?? newRequestId,
    entries: new Map(),
    attempts: new Map(),
    hydrated: new Set(),
    statusChecks: new Map(),
    generation: 0,
    listeners: new Set(),
    cached: null
  };
  return {
    snapshot: () => runtimeSnapshot(runtime),
    subscribe(listener) {
      runtime.listeners.add(listener);
      return () => {
        runtime.listeners.delete(listener);
      };
    },
    entry: (draftId) => entryOf(runtime, draftId),
    resolve: (draft) => resolveDraft(runtime, draft),
    confirm: (draft) => confirm(runtime, draft),
    retry: (draftId) => retryDraft(runtime, draftId),
    reset() {
      runtime.generation += 1;
      runtime.entries.clear();
      runtime.attempts.clear();
      runtime.hydrated.clear();
      runtime.statusChecks.clear();
      publish(runtime);
    },
    dispose() {
      runtime.generation += 1;
      runtime.entries.clear();
      runtime.attempts.clear();
      runtime.hydrated.clear();
      runtime.statusChecks.clear();
      runtime.listeners.clear();
      runtime.cached = null;
    }
  };
}

// ---------------------------------------------------------------------------------------------
// 展示
// ---------------------------------------------------------------------------------------------

const MODE_KEYS: Record<ScheduleMode, string> = {
  REMINDER: 'chat.draft.mode.REMINDER',
  SKILL: 'chat.draft.mode.SKILL',
  AGENTIC: 'chat.draft.mode.AGENTIC'
};

const BUDGET_KEYS: Record<string, string> = {
  max_steps: 'chat.draft.budget.maxSteps',
  max_tool_calls: 'chat.draft.budget.maxToolCalls',
  max_tokens: 'chat.draft.budget.maxTokens'
};

const FIELD_KEYS: Record<string, string> = {
  name: 'chat.draft.field.name',
  mode: 'chat.draft.field.mode',
  delivery_intent: 'chat.draft.field.delivery',
  recurrence: 'chat.draft.field.recurrence',
  context: 'chat.draft.field.context',
  execution_spec: 'chat.draft.field.executionSpec'
};

/** 差异值只做文本化：对象原样 JSON 化（不解析、不猜测语义）。 */
export function formatDiffValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch (error) {
    return String(value);
  }
}

/** 预算只展示调用方拿到的键值；未知键保留原始键名（不静默丢弃）。 */
export function budgetEntries(
  budget: Readonly<Record<string, unknown>>
): { key: string; value: string }[] {
  return Object.entries(budget)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => ({ key, value: formatDiffValue(value) }));
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

function scheduleDraftFieldLabel(t: Translate, field: string): string {
  const key = FIELD_KEYS[field];
  return key === undefined ? field : t(key);
}

function DraftDiff(props: { entries: readonly ScheduleDiffEntry[] }) {
  const { t } = useTranslation();
  if (props.entries.length === 0) return null;
  return (
    <div className="chat-draft-diff" data-testid="chat-draft-diff">
      <span className="chat-draft-section-label">{t('chat.draft.diff')}</span>
      <ul className="chat-draft-diff-list">
        {props.entries.map((entry) => (
          <li key={entry.field} data-testid="chat-draft-diff-row" data-field={entry.field}>
            {t('chat.draft.diffRow', {
              field: scheduleDraftFieldLabel(t, entry.field),
              before: formatDiffValue(entry.before),
              after: formatDiffValue(entry.after)
            })}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DraftPreviewBody(props: { draft: WebchatScheduleDraftReady; preview: SchedulePreview }) {
  const { t } = useTranslation();
  const { preview, draft } = props;
  const dependenciesLabel =
    preview.mode === 'AGENTIC' ? t('chat.draft.allowlist') : t('chat.draft.dependencies');
  const budget = budgetEntries(preview.budget);
  return (
    <div className="chat-draft-facts">
      <p className="chat-draft-what" data-testid="chat-draft-what">
        {preview.what}
      </p>
      <dl className="chat-draft-fact-list">
        <div className="chat-draft-fact">
          <dt>{t('chat.draft.when')}</dt>
          <dd data-testid="chat-draft-when">{preview.when}</dd>
        </div>
        <div className="chat-draft-fact">
          <dt>{t('chat.draft.firstFireAt')}</dt>
          <dd data-testid="chat-draft-first-fire">
            <DateTimeText value={preview.first_fire_at} />
          </dd>
        </div>
        <div className="chat-draft-fact">
          <dt>{t('chat.draft.delivery')}</dt>
          <dd data-testid="chat-draft-delivery">
            {t(
              preview.delivery === 'PUSH'
                ? 'chat.draft.delivery.PUSH'
                : 'chat.draft.delivery.NONE'
            )}
          </dd>
        </div>
        {preview.dependencies.length === 0 ? null : (
          <div className="chat-draft-fact">
            <dt>{dependenciesLabel}</dt>
            <dd data-testid="chat-draft-dependencies">
              <ul className="chat-draft-dependency-list">
                {preview.dependencies.map((dependency) => (
                  <li key={dependency} className="chat-draft-dependency">
                    {dependency}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        {budget.length === 0 ? null : (
          <div className="chat-draft-fact">
            <dt>{t('chat.draft.budget')}</dt>
            <dd data-testid="chat-draft-budget">
              <ul className="chat-draft-budget-list">
                {budget.map((item) => (
                  <li key={item.key} data-budget-key={item.key}>
                    {t(BUDGET_KEYS[item.key] ?? 'chat.draft.budget.other', { key: item.key })}:{' '}
                    {item.value}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        {draft.mode === 'AGENTIC' ? (
          <div className="chat-draft-fact">
            <dt>{t('chat.draft.memory')}</dt>
            <dd data-testid="chat-draft-memory-readonly">{t('chat.draft.memoryReadOnly')}</dd>
          </div>
        ) : null}
      </dl>
      {draft.kind === 'UPDATE' ? <DraftDiff entries={preview.diff} /> : null}
    </div>
  );
}

export interface DraftCardProps {
  draft: WebchatScheduleDraftReady;
  entry: DraftConfirmEntry;
  /** 本地过期判定参考时刻（服务端仍是权威）。 */
  nowMs: number;
  onConfirm(draft: WebchatScheduleDraftReady): void;
  onRetry(draftId: string): void;
}

/** Draft 确认卡片：事实只读 + 显式确认；提交中/已过期/已确认都禁用重复提交。 */
export function DraftCard(props: DraftCardProps) {
  const { t } = useTranslation();
  const { draft, entry } = props;
  const expired = draftExpired(draft.expires_at, props.nowMs);
  const submitted = entry.phase === 'SUBMITTED';
  const submitting = entry.phase === 'SUBMITTING';
  const checking = entry.phase === 'CHECKING';
  const locked =
    checking || submitting || submitted || expired || isDraftFailureFinal(entry.errorCode);
  const finalFailure = isDraftFailureFinal(entry.errorCode);
  return (
    <section
      className="chat-draft-card"
      data-testid="chat-draft-card"
      data-draft-id={draft.draft_id}
      data-kind={draft.kind}
      data-mode={draft.mode}
      data-expired={expired ? 'true' : 'false'}
      data-phase={entry.phase}
      aria-label={t('chat.draft.label')}
    >
      <div className="chat-draft-heading">
        <h3>{t('chat.draft.label')}</h3>
        <span className="chat-draft-mode" data-testid="chat-draft-mode">
          {t(MODE_KEYS[draft.mode])}
        </span>
        <span className="chat-draft-kind" data-testid="chat-draft-kind">
          {t(`chat.draft.kind.${draft.kind}`)}
        </span>
      </div>
      <DraftPreviewBody draft={draft} preview={draft.preview} />
      <p className="chat-draft-expires" data-testid="chat-draft-expires">
        {t('chat.draft.expiresAt')}
        <DateTimeText value={draft.expires_at} />
      </p>
      <div className="chat-draft-actions">
        <Button
          theme="solid"
          size="small"
          data-testid="chat-draft-confirm"
          disabled={locked}
          loading={submitting || checking}
          onClick={() => props.onConfirm(draft)}
        >
          {t(draft.kind === 'UPDATE' ? 'chat.draft.confirm.UPDATE' : 'chat.draft.confirm.CREATE')}
        </Button>
        {entry.phase === 'FAILED' && !finalFailure && !expired ? (
          <Button
            size="small"
            data-testid="chat-draft-retry"
            onClick={() => props.onRetry(draft.draft_id)}
          >
            {t('common.retry')}
          </Button>
        ) : null}
      </div>
      <div className="chat-draft-notices">
        {expired ? (
          <span className="chat-draft-expired" data-testid="chat-draft-expired">
            {t('chat.draft.expired')}
          </span>
        ) : null}
        {submitting ? (
          <span className="chat-draft-submitting" data-testid="chat-draft-submitting">
            {t('chat.draft.submitting')}
          </span>
        ) : null}
        {checking ? (
          <span className="chat-draft-checking" data-testid="chat-draft-checking">
            {t('chat.draft.checking')}
          </span>
        ) : null}
        {submitted ? (
          <span className="chat-draft-submitted" data-testid="chat-draft-submitted">
            {t('chat.draft.submitted')}
          </span>
        ) : null}
        {entry.errorCode === null || expired || submitted ? null : (
          <span
            className="chat-draft-error"
            data-testid="chat-draft-error"
            data-error-code={entry.errorCode}
          >
            {t(draftErrorKey(entry.errorCode))}
          </span>
        )}
      </div>
    </section>
  );
}

/** 历史/流式两处复用的端口：页面持有控制器，展示组件只读状态并转发动作。 */
export interface DraftConfirmationBinding {
  entry(draftId: string): DraftConfirmEntry;
  resolve(draft: WebchatScheduleDraftReady): void;
  confirm(draft: WebchatScheduleDraftReady): void;
  retry(draftId: string): void;
}
