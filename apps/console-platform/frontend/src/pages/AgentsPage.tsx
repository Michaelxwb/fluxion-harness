import { Button, Card, Table, Tag, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

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
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Button theme="solid">{t('common.add')}</Button>
        <Button>{t('common.refresh')}</Button>
      </div>
      <Table
        columns={columns}
        dataSource={[
          { id: 'a1', name: 'MSS Service Agent', key: 'mss-assistant', model: 'Qwen3-235B', enabled: true }
        ]}
        rowKey="id"
        pagination={{ pageSize: 20 }}
      />
      <Typography.Paragraph type="tertiary" style={{ marginTop: 16 }}>
        {t('model.note')}
      </Typography.Paragraph>
    </Card>
  );
}
