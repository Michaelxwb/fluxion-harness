import { Button, Input, SideSheet, Spin, Toast } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { experienceAction, getExperience, replayExperience } from './services/learning';
import type { ExperienceDetail } from './types';

export interface ExperienceDetailSideSheetProps {
  userId: string;
  memoryKey: string | null;
  onClose(): void;
  onMutated(): void;
}

export function ExperienceDetailSideSheet(props: ExperienceDetailSideSheetProps) {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<ExperienceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [reason, setReason] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (props.memoryKey === null) {
      setDetail(null);
      return;
    }
    setLoading(true);
    getExperience(props.userId, props.memoryKey)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => Toast.error(t('learning.errors.load')))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [props.userId, props.memoryKey, t]);

  async function act(action: 'activate' | 'retire' | 'regenerate' | 'replay') {
    if (props.memoryKey === null) return;
    const key = props.memoryKey;
    try {
      if (action === 'replay') {
        await replayExperience(props.userId, key, newRequestId());
      } else {
        if (action !== 'activate' && !reason.trim()) {
          Toast.warning(t('learning.errors.reasonRequired'));
          return;
        }
        await experienceAction(props.userId, key, action, reason || undefined, newRequestId());
      }
      Toast.success(t('learning.actions.done'));
      setReason('');
      props.onMutated();
      setDetail(await getExperience(props.userId, key));
    } catch {
      Toast.error(t('learning.errors.action'));
    }
  }

  return (
    <SideSheet
      visible={props.memoryKey !== null}
      onCancel={props.onClose}
      title={detail?.memory_key ?? t('learning.detail.title')}
      width={720}
    >
      {loading && <Spin />}
      {detail && (
        <div>
          <div className="detail-section-title">{t('learning.detail.identity')}</div>
          <div>{t('learning.columns.family')}: {detail.task_signature ?? '-'}</div>
          <div>{t('learning.columns.state')}: {detail.activation_state}</div>
          <div>{t('learning.columns.authority')}: {detail.authority}</div>
          <div>{t('learning.detail.version')}: {detail.version}</div>
          <div className="detail-section-title">{t('learning.detail.reports')}</div>
          {detail.reports.map((report) => (
            <div key={`${report.version}-${report.owner}`}>
              v{report.version} · {report.status} · {t('learning.detail.owner')}: {report.owner}
            </div>
          ))}
          <div className="detail-section-title">{t('learning.detail.injections')}</div>
          {detail.injections.map((item) => (
            <div key={item.run_id}>
              {item.run_id} · score {item.score ?? '-'} · {item.channel ?? '-'}
            </div>
          ))}
          <div className="detail-section-title">{t('learning.detail.actions')}</div>
          <Input
            value={reason}
            placeholder={t('learning.detail.reason')}
            onChange={setReason}
          />
          <Button onClick={() => act('replay')}>{t('learning.actions.replay')}</Button>
          <Button onClick={() => act('activate')}>{t('learning.actions.activate')}</Button>
          <Button onClick={() => act('retire')}>{t('learning.actions.retire')}</Button>
          <Button onClick={() => act('regenerate')}>{t('learning.actions.regenerate')}</Button>
        </div>
      )}
    </SideSheet>
  );
}
