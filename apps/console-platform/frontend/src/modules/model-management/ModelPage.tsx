import { Button, Input, Popconfirm, Select, Switch, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { RemoteTable } from '../../components/common/RemoteTable';
import { ModelDetailSideSheet, ModelTestResultModal } from './ModelDetailSideSheet';
import { ModelFormModal } from './ModelFormModal';
import {
  batchTestModels,
  deleteModel,
  listModels,
  updateModel,
  type ModelItem,
  type ModelTestResult
} from './services/models';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', enabled: 'ALL', last_test_status: '' };

export function ModelPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [items, setItems] = useState<ModelItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<ModelItem | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formModel, setFormModel] = useState<ModelItem | null>(null);
  const [testResults, setTestResults] = useState<ModelTestResult[]>([]);
  const [testVisible, setTestVisible] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listModels({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        enabled: params.enabled === 'ALL' ? undefined : params.enabled,
        last_test_status: params.last_test_status || undefined
      });
      setItems(page.items);
      setTotal(page.total);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const toggleEnabled = async (model: ModelItem): Promise<void> => {
    await updateModel(model.id, {
      name: model.name,
      base_url: model.base_url,
      model_id: model.model_id,
      expected_revision: model.revision,
      enabled: !model.enabled
    });
    await reload();
  };

  const remove = async (model: ModelItem): Promise<void> => {
    await deleteModel(model.id);
    setDetail(null);
    await reload();
  };

  const runBatchTest = async (): Promise<void> => {
    setTestResults(await batchTestModels(selected));
    setTestVisible(true);
    setSelected([]);
    await reload();
  };

  return (
    <>
      <PageHeader title={t('model.title')} description={t('model.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <>
              <Button
                theme="solid"
                data-testid="create-model"
                onClick={() => {
                  setFormModel(null);
                  setFormVisible(true);
                }}
              >
                {t('model.add')}
              </Button>
              <Button
                data-testid="batch-test"
                disabled={selected.length === 0}
                onClick={() => void runBatchTest()}
              >
                {t('model.batchTest', { count: selected.length })}
              </Button>
            </>
          }
          search={
            <>
              <Input
                value={params.keyword}
                placeholder={t('model.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
              />
              <Select
                value={params.enabled}
                style={{ width: 130 }}
                optionList={[
                  { value: 'ALL', label: t('model.filter.allEnabled') },
                  { value: 'true', label: t('common.status.enabled') },
                  { value: 'false', label: t('common.status.disabled') }
                ]}
                onChange={(value) => setParams((prev) => ({ ...prev, enabled: String(value), page: 1 }))}
              />
              <Select
                value={params.last_test_status || undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('model.columns.lastTestStatus')}
                optionList={['UNTESTED', 'AVAILABLE', 'FAILED'].map((value) => ({ value, label: value }))}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, last_test_status: value ? String(value) : '', page: 1 }))
                }
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<ModelItem>
          rowKey="id"
          loading={loading}
          rowSelection={{
            selectedRowKeys: selected,
            onChange: (keys) => setSelected((keys ?? []).map(String))
          }}
          columns={[
            {
              title: t('model.form.key'),
              dataIndex: 'key',
              render: (value: string, record: ModelItem) => (
                <Button theme="borderless" data-testid={`model-link-${value}`} onClick={() => setDetail(record)}>
                  {value}
                </Button>
              )
            },
            { title: t('model.form.name'), dataIndex: 'name' },
            { title: t('model.form.modelId'), dataIndex: 'model_id' },
            { title: t('model.form.protocol'), dataIndex: 'protocol' },
            { title: t('model.form.baseUrl'), dataIndex: 'base_url', width: 220 },
            {
              title: t('model.form.apiKey'),
              dataIndex: 'api_key_configured',
              render: (value: boolean) => (
                <Tag color={value ? 'green' : 'grey'}>
                  {t(value ? 'model.apiKeyConfigured' : 'model.apiKeyMissing')}
                </Tag>
              )
            },
            {
              title: t('model.form.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean, record: ModelItem) => (
                <Switch size="small" checked={value} onChange={() => void toggleEnabled(record)} />
              )
            },
            {
              title: t('model.columns.lastTestStatus'),
              dataIndex: 'last_test_status',
              render: (value: ModelItem['last_test_status']) => (
                <Tag color={value === 'AVAILABLE' ? 'green' : value === 'FAILED' ? 'red' : 'grey'}>{value}</Tag>
              )
            },
            { title: 'revision', dataIndex: 'revision' },
            {
              title: t('model.columns.updateTime'),
              dataIndex: 'update_time',
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              title: t('model.columns.actions'),
              render: (_: unknown, record: ModelItem) => (
                <>
                  <Button
                    theme="borderless"
                    onClick={() => {
                      setFormModel(record);
                      setFormVisible(true);
                    }}
                  >
                    {t('model.actions.edit')}
                  </Button>
                  <Popconfirm title={t('model.confirmDelete')} onConfirm={() => void remove(record)}>
                    <Button theme="borderless" type="danger">
                      {t('model.actions.delete')}
                    </Button>
                  </Popconfirm>
                </>
              )
            }
          ]}
          dataSource={items}
          page={params.page}
          pageSize={params.page_size}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(page_size) => setParams((prev) => ({ ...prev, page: 1, page_size }))}
          empty={<EmptyState title={t('common.empty')} description={t('common.emptyHint')} />}
        />
      </PageSection>
      <ModelFormModal
        visible={formVisible}
        model={formModel}
        onCancel={() => {
          setFormVisible(false);
          setFormModel(null);
        }}
        onSaved={() => {
          setFormVisible(false);
          setFormModel(null);
          setDetail(null);
          void reload();
        }}
      />
      <ModelDetailSideSheet
        model={detail}
        onCancel={() => setDetail(null)}
        onEdit={(model) => {
          setFormModel(model);
          setFormVisible(true);
        }}
        onDelete={(model) => void remove(model)}
      />
      <ModelTestResultModal
        visible={testVisible}
        results={testResults}
        onCancel={() => setTestVisible(false)}
      />
    </>
  );
}
