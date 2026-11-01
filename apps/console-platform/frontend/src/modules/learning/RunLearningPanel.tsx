import { Spin, Tag } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { RunLearningActions } from './RunLearningActions';
import { getRunLearningDiagnostics, type RunLearningDiagnostics } from './services/learning';

export function RunLearningPanel(props: { runId: string }) {
  const { t } = useTranslation();
  const [data, setData] = useState<RunLearningDiagnostics | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRunLearningDiagnostics(props.runId)
      .then((value) => {
        if (!cancelled) setData(value);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [props.runId]);

  if (data === null) {
    return <Spin />;
  }
  return (
    <div>
      <div className="detail-section-title">{t('learning.run.family')}</div>
      <div>{data.task_family ?? '-'}</div>
      {!data.recorded && <Tag color="orange">{t('learning.run.notRecorded')}</Tag>}
      <div className="detail-section-title">{t('learning.run.injected')}</div>
      {data.injected.map((item, index) => (
        <div key={index}>
          {String(item.memory_key)} · score {String(item.score ?? '-')} · {String(item.channel ?? '-')}
        </div>
      ))}
      <div className="detail-section-title">{t('learning.run.skipped')}</div>
      {data.skipped.map((item, index) => (
        <div key={index}>
          {String(item.memory_key)} · {String(item.reason ?? '-')}
        </div>
      ))}
      <div className="detail-section-title">{t('learning.run.actions')}</div>
      <RunLearningActions runId={props.runId} />
    </div>
  );
}
