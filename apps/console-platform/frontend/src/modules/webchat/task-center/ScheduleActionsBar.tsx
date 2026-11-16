/**
 * 生命周期动作条（design frontend §3.6；TASK-028）。
 *
 * 取消必须让用户选范围：`FUTURE_ONLY`（默认，保留进行中与待处理）或 `ALL_ACTIVE`
 * （终止全部）。旧版那种"是否终止当前执行"的布尔语义在这里不存在——服务端只认这两个范围。
 * 终态任务只读：`ENDED/CANCELLED` 不提供暂停/恢复，ONCE 终态只提供"重新创建"。
 */

import { Button, Modal, Radio, RadioGroup, Typography } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ScheduleStatus } from '../types';
import type { CancelScope, ScheduleActionName } from './actionsState';

export interface ScheduleActionsBarProps {
  status: ScheduleStatus | string;
  mode: string;
  submitting: ScheduleActionName | null;
  errorCode: string | null;
  onAction(action: ScheduleActionName, scope?: CancelScope): void;
  onRecreate(): void;
  onEdit(): void;
}

const TERMINAL = new Set(['ENDED', 'CANCELLED']);

/** 错误码 → 词条键的静态映射：动态拼接键会让 i18n 死键检查与翻译提取都失效。 */
const ACTION_ERROR_KEYS: Record<string, string> = {
  SCHEDULE_STATE_INVALID: 'chat.taskCenter.actions.error.SCHEDULE_STATE_INVALID',
  SCHEDULE_ADMISSION_FAILED: 'chat.taskCenter.actions.error.SCHEDULE_ADMISSION_FAILED',
  SCHEDULE_VERSION_CONFLICT: 'chat.taskCenter.actions.error.SCHEDULE_VERSION_CONFLICT',
  UNKNOWN: 'chat.taskCenter.actions.error.unknown'
};

export function ScheduleActionsBar(props: ScheduleActionsBarProps) {
  const { t } = useTranslation();
  const [cancelling, setCancelling] = useState(false);
  const [scope, setScope] = useState<CancelScope>('FUTURE_ONLY');
  const terminal = TERMINAL.has(String(props.status));
  const paused = props.status === 'PAUSED';
  const busy = props.submitting !== null;

  return (
    <div className="task-center-actions" data-testid="schedule-actions">
      {terminal ? (
        <>
          <Typography.Text type="tertiary">{t('chat.taskCenter.actions.readOnly')}</Typography.Text>
          <Button onClick={props.onRecreate}>{t('chat.taskCenter.actions.recreate')}</Button>
        </>
      ) : (
        <>
          {paused || props.status === 'NEEDS_ATTENTION' ? (
            <Button
              loading={props.submitting === 'resume'}
              onClick={() => props.onAction('resume')}
              data-testid="schedule-action-resume"
            >
              {t('chat.taskCenter.actions.resume')}
            </Button>
          ) : (
            <Button
              loading={props.submitting === 'pause'}
              onClick={() => props.onAction('pause')}
              data-testid="schedule-action-pause"
            >
              {t('chat.taskCenter.actions.pause')}
            </Button>
          )}
          <Button onClick={props.onEdit} data-testid="schedule-action-edit">
            {t('chat.taskCenter.actions.edit')}
          </Button>
          <Button
            type="danger"
            disabled={busy}
            onClick={() => {
              setScope('FUTURE_ONLY');
              setCancelling(true);
            }}
            data-testid="schedule-action-cancel"
          >
            {t('chat.taskCenter.actions.cancel')}
          </Button>
        </>
      )}
      {props.errorCode ? (
        <Typography.Text type="danger" data-testid="schedule-action-error">
          {t(ACTION_ERROR_KEYS[props.errorCode] ?? ACTION_ERROR_KEYS.UNKNOWN)}
        </Typography.Text>
      ) : null}
      <Modal
        visible={cancelling}
        title={t('chat.taskCenter.actions.cancelTitle')}
        onCancel={() => setCancelling(false)}
        footer={
          <Button
            type="danger"
            onClick={() => {
              setCancelling(false);
              props.onAction('cancel', scope);
            }}
          >
            {t('chat.taskCenter.actions.cancelConfirm')}
          </Button>
        }
      >
        <RadioGroup
          value={scope}
          onChange={(event) => setScope(event.target.value as CancelScope)}
        >
          <Radio value="FUTURE_ONLY">{t('chat.taskCenter.actions.scopeFutureOnly')}</Radio>
          <Radio value="ALL_ACTIVE">{t('chat.taskCenter.actions.scopeAllActive')}</Radio>
        </RadioGroup>
      </Modal>
    </div>
  );
}
