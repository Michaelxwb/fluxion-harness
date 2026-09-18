import { Banner, Button, Modal, Popconfirm, Table, Tabs, Tag } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import type { ModelItem, ModelTestResult } from './services/models';

export interface ModelDetailSideSheetProps {
  model: ModelItem | null;
  onCancel(): void;
  onEdit(model: ModelItem): void;
  onDelete(model: ModelItem): void;
}

function testStatusColor(status: ModelItem['last_test_status']): 'grey' | 'green' | 'red' {
  if (status === 'AVAILABLE') {
    return 'green';
  }
  if (status === 'FAILED') {
    return 'red';
  }
  return 'grey';
}

function testStatusKey(status: ModelItem['last_test_status']): string {
  if (status === 'AVAILABLE') {
    return 'model.test.status.available';
  }
  if (status === 'FAILED') {
    return 'model.test.status.failed';
  }
  return 'model.test.status.untested';
}

export function ModelDetailSideSheet(props: ModelDetailSideSheetProps) {
  const { t } = useTranslation();
  const model = props.model;
  if (model === null) {
    return null;
  }
  return (
    <DetailSideSheet
      visible
      title={model.name}
      subtitle={`${model.key} · ${model.model_id}`}
      activeTab="basic"
      actions={
        <>
          <Button theme="borderless" onClick={() => props.onEdit(model)}>
            {t('model.actions.edit')}
          </Button>
          <Popconfirm title={t('model.confirmDelete')} onConfirm={() => props.onDelete(model)}>
            <Button theme="borderless" type="danger">
              {t('model.actions.delete')}
            </Button>
          </Popconfirm>
        </>
      }
      onCancel={props.onCancel}
    >
      <Tabs.TabPane itemKey="basic" tab={t('model.detail.basic')}>
        <div className="detail-section-title">{t('model.detail.basic')}</div>
        <DetailGrid
          items={[
            { label: t('model.form.name'), value: model.name },
            { label: t('model.form.key'), value: model.key },
            { label: t('model.form.protocol'), value: model.protocol },
            { label: t('model.form.modelId'), value: model.model_id },
            {
              label: t('model.form.enabled'),
              value: (
                <Tag color={model.enabled ? 'green' : 'grey'}>
                  {t(model.enabled ? 'common.status.enabled' : 'common.status.disabled')}
                </Tag>
              )
            },
            {
              label: t('model.columns.lastTestStatus'),
              value: (
                <Tag color={testStatusColor(model.last_test_status)}>
                  {t(testStatusKey(model.last_test_status))}
                </Tag>
              )
            },
            { label: t('model.detail.revision'), value: `r${model.revision}` },
            {
              label: t('model.form.apiKey'),
              value: model.api_key_configured ? t('model.apiKeyConfigured') : t('model.apiKeyMissing')
            },
            { label: t('model.columns.updateTime'), value: <DateTimeText value={model.update_time} /> },
            { label: t('model.form.baseUrl'), value: model.base_url }
          ]}
        />
        <Banner type="info" description={t('model.detail.hint')} />
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}

export interface ModelTestResultModalProps {
  visible: boolean;
  results: ModelTestResult[];
  onCancel(): void;
}

export function ModelTestResultModal(props: ModelTestResultModalProps) {
  const { t } = useTranslation();
  return (
    <Modal
      visible={props.visible}
      title={t('model.test.resultTitle')}
      footer={null}
      onCancel={props.onCancel}
      width={640}
    >
      <Table
        dataSource={props.results}
        rowKey="model_id"
        pagination={false}
        columns={[
          { title: t('model.form.modelId'), dataIndex: 'model_id' },
          {
            title: t('model.columns.lastTestStatus'),
            dataIndex: 'status',
            render: (value: ModelTestResult['status']) => (
              <Tag color={value === 'AVAILABLE' ? 'green' : 'red'}>{value}</Tag>
            )
          },
          { title: t('model.test.latency'), dataIndex: 'latency_ms' },
          { title: t('model.test.errorCode'), dataIndex: 'error_code', render: (value: string | null) => value ?? '-' }
        ]}
      />
    </Modal>
  );
}
