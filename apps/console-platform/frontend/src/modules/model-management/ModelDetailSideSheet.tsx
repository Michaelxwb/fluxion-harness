import { IconAlertCircle, IconStopwatchStroked, IconTickCircle } from '@douyinfe/semi-icons';
import { Banner, Button, Modal, Tabs } from '@douyinfe/semi-ui';
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
  /** 打开时默认页签；新增向导成功后直接落到“成员”确认绑定结果。 */
  initialTab?: 'basic' | 'members';
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
  // 换一个模型或指定了新的入口页签就重置：成员页签只在 ADMIN 下存在
  useEffect(() => {
    setActiveTab(props.initialTab ?? 'basic');
  }, [model?.id, props.initialTab]);
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

export interface ModelTestResultLabel {
  key: string;
  name: string;
}

export interface ModelTestResultModalProps {
  visible: boolean;
  results: ModelTestResult[];
  /** model_id → 模型标识/名称；缺省时回退展示 model_id。 */
  labels?: Record<string, ModelTestResultLabel>;
  onCancel(): void;
}

export function ModelTestResultModal(props: ModelTestResultModalProps) {
  const { t } = useTranslation();
  const passed = props.results.filter((result) => result.status === 'AVAILABLE').length;
  const failed = props.results.length - passed;
  const maxLatency = props.results.reduce(
    (max, result) => Math.max(max, result.latency_ms),
    0
  );
  return (
    <Modal
      centered
      className="model-test-modal"
      visible={props.visible}
      title={t('model.test.resultTitle')}
      width={620}
      style={{ maxWidth: 'calc(100vw - 32px)' }}
      footer={
        <div className="model-test-footer">
          <Button type="primary" onClick={props.onCancel}>
            {t('common.close')}
          </Button>
        </div>
      }
      onCancel={props.onCancel}
    >
      <div className="model-test-summary">
        <span className="model-test-summary-item model-test-summary-passed">
          <IconTickCircle />
          {t('model.test.summary.passed', { count: passed })}
        </span>
        <span className="model-test-summary-item model-test-summary-failed">
          <IconAlertCircle />
          {t('model.test.summary.failed', { count: failed })}
        </span>
        <span className="model-test-summary-item model-test-summary-latency">
          <IconStopwatchStroked />
          {t('model.test.summary.maxLatency', { ms: maxLatency })}
        </span>
      </div>
      <div className="model-test-scope">{t('model.test.scopeHint')}</div>
      {props.results.length === 0 ? (
        <div className="model-test-empty">{t('model.test.empty')}</div>
      ) : (
        <div className="model-test-results">
          {props.results.map((result) => {
            const label = props.labels?.[result.model_id] ?? {
              key: result.model_id,
              name: ''
            };
            const available = result.status === 'AVAILABLE';
            return (
              <div
                key={result.model_id}
                data-testid={`model-test-result-${result.model_id}`}
                className={`model-test-result ${
                  available ? 'model-test-result-passed' : 'model-test-result-failed'
                }`}
              >
                <div className="model-test-result-main">
                  <div className="model-test-result-model">
                    <div className="model-test-result-key">{label.key}</div>
                    {label.name ? (
                      <div className="model-test-result-name">{label.name}</div>
                    ) : null}
                  </div>
                  <div className="model-test-result-status">
                    {available ? <IconTickCircle /> : <IconAlertCircle />}
                    <span>{testStatusOptions(t)[result.status].label}</span>
                  </div>
                  <div className="model-test-result-latency">{result.latency_ms} ms</div>
                </div>
                {result.error_code ? (
                  <div className="model-test-result-error">{result.error_code}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
