import { Button, Table, Tag, Tooltip } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EntityLink } from '../../components/common/EntityLink';
import { authorityLabel, familyLabel, reportStatusLabel, stateLabel, stateTone } from './labels';
import type { ActivationState, ExperienceItem } from './types';

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
        title={t('learning.columns.experience')}
        dataIndex="content"
        render={(content: ExperienceItem['content'], item: ExperienceItem) => (
          <Tooltip content={item.memory_key}>
            <EntityLink onClick={() => props.onOpen(item)}>
              {String(content?.value ?? item.memory_key)}
            </EntityLink>
          </Tooltip>
        )}
      />
      <Table.Column
        title={t('learning.columns.family')}
        dataIndex="task_signature"
        render={(value: string | null) => <Tooltip content={value ?? ''}>{familyLabel(t, value)}</Tooltip>}
      />
      <Table.Column
        title={t('learning.columns.state')}
        dataIndex="activation_state"
        render={(value: ActivationState) => (
          <Tag color={stateTone(value)}>{stateLabel(t, value)}</Tag>
        )}
      />
      <Table.Column
        title={t('learning.columns.authority')}
        dataIndex="authority"
        render={(value: string) => authorityLabel(t, value)}
      />
      <Table.Column
        title={t('learning.columns.reportStatus')}
        dataIndex="latest_report"
        render={(report: ExperienceItem['latest_report']) =>
          report ? reportStatusLabel(t, report.status) : '-'
        }
      />
      <Table.Column
        title={t('learning.columns.updated')}
        dataIndex="update_time"
        render={(value: string) => <DateTimeText value={value} />}
      />
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
