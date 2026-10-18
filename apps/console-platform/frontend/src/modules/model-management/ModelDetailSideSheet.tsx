import { Banner, Modal, Table, Tabs } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { StatusTag } from '../../components/common/StatusTag';
import { ModelDeploymentPanel } from './ModelDeploymentPanel';
import { readModelProviderKey, readOutputBudget } from './services/modelDeployments';
import type { ModelItem, ModelTestResult } from './services/models';
import { enabledOptions, testStatusOptions } from './statusOptions';

export interface ModelDetailSideSheetProps {
  model: ModelItem | null;
  onCancel(): void;
  /** 成员关系变更后的通知（页面按需刷新列表/详情）；缺省无操作。 */
  onChanged?(): void;
}

export function ModelDetailSideSheet(props: ModelDetailSideSheetProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const model = props.model;
  const [activeTab, setActiveTab] = useState('basic');
  // 换一个模型就是一次新详情：回到基本信息页签（成员页签只在 ADMIN 下存在）
  useEffect(() => {
    setActiveTab('basic');
  }, [model?.id]);
  if (model === null) {
    return null;
  }
  const providerKey = readModelProviderKey(model);
  const outputBudget = readOutputBudget(model.params);
  return (
    <DetailSideSheet
      visible
      title={model.name}
      subtitle={`${model.key} · ${model.model_id}`}
      activeTab={activeTab}
      onTabChange={(key) => setActiveTab(key)}
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
            { label: t('model.detail.provider'), value: providerKey === '' ? '-' : providerKey },
            {
              label: t('model.detail.outputBudget'),
              value: outputBudget === null ? t('model.detail.outputBudgetMissing') : outputBudget
            },
            {
              label: t('model.form.enabled'),
              value: <StatusTag status={model.enabled} options={enabledOptions(t)} />
            },
            {
              label: t('model.columns.lastTestStatus'),
              value: <StatusTag status={model.last_test_status} options={testStatusOptions(t)} />
            },
            { label: t('model.detail.revision'), value: `r${model.revision}` },
            { label: t('model.columns.updateTime'), value: <DateTimeText value={model.update_time} /> }
          ]}
        />
        <Banner type="info" description={t('model.detail.hint')} />
      </Tabs.TabPane>
      {isAdmin ? (
        <Tabs.TabPane itemKey="members" tab={t('model.detail.members')}>
          <div className="detail-section-title">{t('model.detail.members')}</div>
          <ModelDeploymentPanel
            modelId={model.id}
            providerKey={providerKey}
            onChanged={props.onChanged}
          />
        </Tabs.TabPane>
      ) : null}
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
      centered
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
