import type { StatusTagOption } from '../../components/common/StatusTag';

type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * 枚举 → StatusTag 选项的**唯一**映射处（设计 §3.5 I18N-01）：Run 状态、operation 状态、
 * 关联 Task 状态都从这里取，避免同一枚举在多处各写一套颜色/名称。
 *
 * 标签一律由调用方注入**本次渲染**的 `t` 解析（`useTranslation()`），本模块不在模块常量或
 * state/memo 里缓存译文——否则 `changeLanguage` 后标签不会跟着切换。
 */

const RUN_STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  CREATED: 'grey',
  RUNNING: 'blue',
  WAITING_TOOL: 'amber',
  WAITING_INPUT: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

const OPERATION_STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  SUBMIT_PENDING: 'grey',
  SUBMITTED: 'blue',
  TASK_ACCEPTED: 'blue',
  RUNNING: 'blue',
  RESULT_RECEIVED: 'blue',
  MATERIALIZED: 'blue',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey',
  LATE: 'amber'
};

const TASK_STATUS_COLORS: Record<string, StatusTagOption['color']> = {
  QUEUED: 'grey',
  RUNNING: 'blue',
  WAITING: 'amber',
  COMPLETED: 'green',
  FAILED: 'red',
  CANCELLED: 'grey'
};

export function runStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    Object.entries(RUN_STATUS_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`run.status.${status}`) }
    ])
  );
}

export function operationStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    Object.entries(OPERATION_STATUS_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`run.operation.status.${status}`) }
    ])
  );
}

/**
 * 关联 Task 状态复用任务模块词条。**未登记取值不得冒充任何已知状态**——尤其不能被显示成
 * 「任务失败」：回退为「未知任务状态（原值）」的安全标识，等前端枚举跟上再本地化。
 */
export function taskStatusOptions(t: Translate): Record<string, StatusTagOption> {
  return Object.fromEntries(
    Object.entries(TASK_STATUS_COLORS).map(([status, color]) => [
      status,
      { color, label: t(`task.status.${status}`) }
    ])
  );
}

export function taskStatusFallback(t: Translate, status: string): StatusTagOption {
  return { color: 'grey', label: t('run.operation.unknownTaskStatus', { status }) };
}

/** 未登记的 operation 状态同样按安全标识回退，不冒充 COMPLETED/FAILED。 */
export function operationStatusFallback(t: Translate, status: string): StatusTagOption {
  return { color: 'grey', label: t('run.operation.unknownStatus', { status }) };
}

export function completionModeLabel(t: Translate, mode: string): string {
  return t(`run.operation.mode.${mode}`);
}

/** 提交/执行错误阶段（SUBMIT/EXECUTE）：只翻译阶段，不把提交失败说成任务失败。 */
export function errorPhaseLabel(t: Translate, phase: string): string {
  return t(`run.operation.errorPhase.${phase}`);
}
