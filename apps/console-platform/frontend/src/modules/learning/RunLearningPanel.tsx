import { Spin, Tag } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DataTable } from '../../components/common/DataTable';
import { DetailGrid } from '../../components/common/DetailGrid';
import { RunLearningActions } from './RunLearningActions';
import { channelLabel, familyLabel, scoreLabel, skipReasonLabel } from './labels';
import { getRunLearningDiagnostics, type RunLearningDiagnostics } from './services/learning';

export function RunLearningPanel(props: { runId: string }) {
  const { t } = useTranslation();
  const [data, setData] = useState<RunLearningDiagnostics | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRunLearningDiagnostics(props.runId, { errorTip: 'always' })
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
      <DetailGrid
        items={[
          { label: t('learning.run.family'), value: familyLabel(t, data.task_family) },
          {
            label: t('learning.columns.state'),
            value: data.recorded ? t('learning.run.recorded') : <Tag color="orange">{t('learning.run.notRecorded')}</Tag>
          },
          {
            label: t('learning.run.enabled'),
            value: data.learning_enabled
              ? t('learning.run.switchOn')
              : t('learning.run.switchOff')
          },
          { label: t('learning.run.threshold'), value: scoreLabel(data.min_score) }
        ]}
      />
      <div className="detail-section-title">{t('learning.run.injected')}</div>
      <DataTable
        dataSource={data.injected}
        pagination={false}
        rowKey="memory_key"
        columns={[
          { title: t('learning.columns.key'), dataIndex: 'memory_key' },
          {
            title: t('learning.detail.score'),
            dataIndex: 'score',
            render: (value: number | null) => scoreLabel(value)
          },
          {
            title: t('learning.detail.channel'),
            dataIndex: 'channel',
            render: (value: string | null) => channelLabel(t, value)
          }
        ]}
      />
      <div className="detail-section-title">{t('learning.run.skipped')}</div>
      <DataTable
        dataSource={data.skipped}
        pagination={false}
        rowKey="memory_key"
        columns={[
          { title: t('learning.columns.key'), dataIndex: 'memory_key' },
          {
            title: t('learning.detail.reason'),
            dataIndex: 'reason',
            render: (value: string | null) => skipReasonLabel(t, value)
          },
          {
            title: t('learning.detail.score'),
            dataIndex: 'score',
            render: (value: number | null) => scoreLabel(value)
          }
        ]}
      />
      <div className="detail-section-title">{t('learning.run.actions')}</div>
      <RunLearningActions runId={props.runId} />
    </div>
  );
}
