import { Button, Input, Toast } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { feedbackRun, stopRun } from './services/learning';

export function RunLearningActions(props: { runId: string; onMutated?(): void }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');

  async function submit(action: 'stop' | 'feedback') {
    if (!reason.trim()) {
      Toast.warning(t('learning.errors.reasonRequired'));
      return;
    }
    try {
      if (action === 'stop') {
        await stopRun(props.runId, reason, newRequestId());
      } else {
        await feedbackRun(props.runId, reason, newRequestId());
      }
      Toast.success(t('learning.actions.done'));
      setReason('');
      props.onMutated?.();
    } catch {
      Toast.error(t('learning.errors.action'));
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <Input value={reason} placeholder={t('learning.detail.reason')} onChange={setReason} />
      <Button onClick={() => submit('stop')}>{t('learning.actions.stop')}</Button>
      <Button onClick={() => submit('feedback')}>{t('learning.actions.feedback')}</Button>
    </div>
  );
}
