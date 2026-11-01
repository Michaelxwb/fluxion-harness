import { Button, Table, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DataTable } from '../../components/common/DataTable';
import { EntityLink } from '../../components/common/EntityLink';
import type { ActivationState, ExperienceItem } from './types';

const STATE_TONE: Record<
  string,
  { color: 'grey' | 'blue' | 'green' | 'orange'; labelKey: string }
> = {
  PROPOSED: { color: 'grey', labelKey: 'learning.state.PROPOSED' },
  TRIAL: { color: 'blue', labelKey: 'learning.state.TRIAL' },
  ACTIVE: { color: 'green', labelKey: 'learning.state.ACTIVE' },
  RETIRED: { color: 'orange', labelKey: 'learning.state.RETIRED' }
};

export interface ExperienceListProps {
  items: ExperienceItem[];
  loading?: boolean;
  onOpen(item: ExperienceItem): void;
}

export function ExperienceList(props: ExperienceListProps) {
  const { t } = useTranslation();
  return (
    <DataTable
      dataSource={props.items}
      collapseActions={false}
      pagination={false}
      loading={props.loading}
      rowKey="memory_key"
      onRow={(item) => (item ? { onClick: () => props.onOpen(item) } : {})}
    >
      <Table.Column
        title={t('learning.columns.key')}
        dataIndex="memory_key"
        render={(value: string, item: ExperienceItem) => (
          <EntityLink onClick={() => props.onOpen(item)}>{value}</EntityLink>
        )}
      />
      <Table.Column title={t('learning.columns.family')} dataIndex="task_signature" />
      <Table.Column
        title={t('learning.columns.state')}
        dataIndex="activation_state"
        render={(value: ActivationState) => {
          const tone = STATE_TONE[value] ?? { color: 'grey', labelKey: 'learning.state.UNKNOWN' };
          return <Tag color={tone.color}>{t(tone.labelKey, { value })}</Tag>;
        }}
      />
      <Table.Column title={t('learning.columns.authority')} dataIndex="authority" />
      <Table.Column
        title={t('learning.columns.report')}
        dataIndex="latest_report"
        render={(report: ExperienceItem['latest_report']) => report?.status ?? '-'}
      />
      <Table.Column title={t('learning.columns.updated')} dataIndex="update_time" />
      <Table.Column
        title={t('learning.columns.actions')}
        key="actions"
        fixed="right"
        width={90}
        render={(_: unknown, item: ExperienceItem) => (
          <Button theme="borderless" onClick={() => props.onOpen(item)}>
            {t('learning.actions.view')}
          </Button>
        )}
      />
    </DataTable>
  );
}
