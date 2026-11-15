import { Modal, Radio, RadioGroup } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { CancelScope } from './services/schedules';

/** 两档取消（design §3.6 CMP-13）：默认只取消未来触发，终止全部必须由用户显式选择。 */
const DEFAULT_SCOPE: CancelScope = 'FUTURE_ONLY';
const SCOPES: CancelScope[] = ['FUTURE_ONLY', 'ALL_ACTIVE'];

export interface CancelScheduleModalProps {
  scheduleId: string | null;
  loading?: boolean;
  onDismiss(): void;
  onConfirm(scope: CancelScope): void;
}

export function CancelScheduleModal(props: CancelScheduleModalProps) {
  const { t } = useTranslation();
  const [scope, setScope] = useState<CancelScope>(DEFAULT_SCOPE);

  const close = (): void => {
    setScope(DEFAULT_SCOPE);
    props.onDismiss();
  };

  return (
    <Modal
      centered
      visible={props.scheduleId !== null}
      title={t('schedule.cancel.title')}
      okText={t('schedule.cancel.confirm')}
      cancelText={t('common.cancel')}
      confirmLoading={props.loading}
      onCancel={close}
      onOk={() => props.onConfirm(scope)}
    >
      <div data-testid="schedule-cancel-modal">
        <p>{t('schedule.cancel.content')}</p>
        <RadioGroup
          value={scope}
          onChange={(event) => setScope(String(event.target.value) as CancelScope)}
        >
          {SCOPES.map((option) => (
            <Radio key={option} value={option} data-testid={`schedule-cancel-scope-${option}`}>
              {t(`schedule.cancel.scope.${option}`)}
            </Radio>
          ))}
        </RadioGroup>
        <p style={{ color: 'var(--semi-color-text-2)', marginTop: 8 }}>
          {t(`schedule.cancel.scopeHint.${scope}`)}
        </p>
      </div>
    </Modal>
  );
}
