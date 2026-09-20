import { Banner, Button, Modal, Table, Tabs } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { StatusTag } from '../../components/common/StatusTag';
import type { ModelItem, ModelTestResult } from './services/models';
import { apiKeyOptions, enabledOptions, testStatusOptions } from './statusOptions';

export interface ModelDetailSideSheetProps {
  model: ModelItem | null;
  onCancel(): void;
  onEdit(model: ModelItem): void;
  onDelete(model: ModelItem): void;
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
          <ConfirmAction theme="light" danger title={t('model.confirmDelete')} onConfirm={() => props.onDelete(model)}>
            {t('model.actions.delete')}
          </ConfirmAction>
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
              value: <StatusTag status={model.enabled} options={enabledOptions(t)} />
            },
            {
              label: t('model.columns.lastTestStatus'),
              value: <StatusTag status={model.last_test_status} options={testStatusOptions(t)} />
            },
            { label: t('model.detail.revision'), value: `r${model.revision}` },
            {
              label: t('model.form.apiKey'),
              value: <StatusTag status={model.api_key_configured} options={apiKeyOptions(t)} />
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
  /** model_id → 展示用标签（`key · name`）；缺省时回退展示 model_id。 */
  labels?: Record<string, string>;
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
          {
            title: t('model.columns.key'),
            dataIndex: 'model_id',
            render: (value: string) => props.labels?.[value] ?? value
          },
          {
            title: t('model.columns.lastTestStatus'),
            dataIndex: 'status',
            render: (value: ModelTestResult['status']) => (
              <StatusTag status={value} options={testStatusOptions(t)} />
            )
          },
          { title: t('model.test.latency'), dataIndex: 'latency_ms' },
          { title: t('model.test.errorCode'), dataIndex: 'error_code', render: (value: string | null) => value ?? '-' }
        ]}
      />
    </Modal>
  );
}
