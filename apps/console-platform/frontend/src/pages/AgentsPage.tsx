import { Button, Table, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { ModuleToolbar } from '../components/common/ModuleToolbar';
import { PageCard } from '../components/common/PageCard';

export function AgentsPage() {
  const { t } = useTranslation();
  const columns = [
    { title: t('agent.fields.name'), dataIndex: 'name' },
    { title: t('agent.fields.key'), dataIndex: 'key' },
    { title: t('agent.fields.model'), dataIndex: 'model' },
    {
      title: t('agent.fields.enabled'),
      dataIndex: 'enabled',
      render: (value: boolean) => (
        <Tag color={value ? 'green' : 'grey'}>
          {t(value ? 'common.status.enabled' : 'common.status.disabled')}
        </Tag>
      )
    }
  ];

  return (
    <PageCard title={t('nav.agent')} subtitle={t('model.note')}>
      <ModuleToolbar
        actions={<Button theme="solid">{t('common.add')}</Button>}
        search={<Button>{t('common.refresh')}</Button>}
      />
      <Table
        columns={columns}
        dataSource={[
          { id: 'a1', name: 'MSS Service Agent', key: 'mss-assistant', model: 'Qwen3-235B', enabled: true }
        ]}
        rowKey="id"
        pagination={{ pageSize: 20 }}
      />
    </PageCard>
  );
}
