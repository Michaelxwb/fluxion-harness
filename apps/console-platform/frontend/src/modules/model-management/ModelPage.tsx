import { Button, Input, Select, Switch, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { RemoteTable } from '../../components/common/RemoteTable';
import { StatusTag } from '../../components/common/StatusTag';
import { ModelDetailSideSheet, ModelTestResultModal } from './ModelDetailSideSheet';
import { ModelFormModal } from './ModelFormModal';
import { apiKeyOptions, testStatusOptions } from './statusOptions';
import {
  batchTestModels,
  deleteModel,
  getModel,
  listModels,
  updateModel,
  type ModelItem,
  type ModelTestResult
} from './services/models';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', enabled: 'ALL', last_test_status: '' };
const EMPTY_FILTERS = { keyword: '', enabled: 'ALL', last_test_status: '' };

export function ModelPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [items, setItems] = useState<ModelItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ModelItem | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formModel, setFormModel] = useState<ModelItem | null>(null);
  const [testResults, setTestResults] = useState<ModelTestResult[]>([]);
  const [testVisible, setTestVisible] = useState(false);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const page = await listModels({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        enabled: params.enabled === 'ALL' ? undefined : params.enabled,
        last_test_status: params.last_test_status || undefined
      });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
      setItems([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 换页/换筛选后，之前勾选的模型可能已不在当前页 → 清空选择，避免对不可见的模型发起批量测试
  useEffect(() => {
    setSelected([]);
  }, [params]);

  const applyFilters = (): void => {
    setParams((prev) => ({
      ...prev,
      keyword: filters.keyword,
      enabled: filters.enabled,
      last_test_status: filters.last_test_status,
      page: 1
    }));
  };

  const resetFilters = (): void => {
    setFilters(EMPTY_FILTERS);
    setParams(DEFAULT_PARAMS);
  };

  const openDetail = async (id: string): Promise<void> => {
    try {
      setDetail(await getModel(id));
    } catch {
      Toast.error(t('common.loadFailed'));
    }
  };

  const refreshDetail = async (modelId: string): Promise<void> => {
    if (detail?.id !== modelId) {
      return;
    }
    try {
      setDetail(await getModel(modelId));
    } catch {
      Toast.error(t('common.loadFailed'));
    }
  };

  const toggleEnabled = async (model: ModelItem): Promise<void> => {
    setSavingId(model.id);
    try {
      await updateModel(model.id, {
        name: model.name,
        base_url: model.base_url,
        model_id: model.model_id,
        expected_revision: model.revision,
        enabled: !model.enabled
      });
      await refreshDetail(model.id);
      await reload();
    } catch {
      Toast.error(t('common.saveFailed'));
    } finally {
      setSavingId(null);
    }
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

  const testLabels: Record<string, string> = Object.fromEntries(
    items.map((model) => [model.id, `${model.key} · ${model.name}`])
  );

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
                value={filters.keyword}
                placeholder={t('model.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={(value) => setFilters((prev) => ({ ...prev, keyword: value }))}
                onEnterPress={applyFilters}
              />
              <Select
                value={filters.enabled}
                style={{ width: 130 }}
                optionList={[
                  { value: 'ALL', label: t('model.filter.allEnabled') },
                  { value: 'true', label: t('common.status.enabled') },
                  { value: 'false', label: t('common.status.disabled') }
                ]}
                onChange={(value) => setFilters((prev) => ({ ...prev, enabled: String(value) }))}
              />
              <Select
                value={filters.last_test_status || undefined}
                style={{ width: 150 }}
                showClear
                placeholder={t('model.columns.lastTestStatus')}
                optionList={Object.entries(testStatusOptions(t)).map(([value, option]) => ({
                  value,
                  label: option.label
                }))}
                onChange={(value) =>
                  setFilters((prev) => ({ ...prev, last_test_status: value ? String(value) : '' }))
                }
              />
              <Button data-testid="search-model" onClick={applyFilters}>
                {t('common.search')}
              </Button>
              <Button data-testid="reset-model" onClick={resetFilters}>
                {t('common.reset')}
              </Button>
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        {failed ? (
          <ErrorState onRetry={() => void reload()} />
        ) : (
          <RemoteTable<ModelItem>
            rowKey="id"
            loading={loading}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected((keys ?? []).map(String))
            }}
            columns={[
              {
                title: t('model.columns.key'),
                dataIndex: 'key',
                render: (value: string, record: ModelItem) => (
                  <EntityLink testId={`model-link-${value}`} onClick={() => void openDetail(record.id)}>
                    {value}
                  </EntityLink>
                )
              },
              { title: t('model.columns.name'), dataIndex: 'name' },
              { title: t('model.columns.modelId'), dataIndex: 'model_id' },
              { title: t('model.columns.protocol'), dataIndex: 'protocol' },
              { title: t('model.columns.baseUrl'), dataIndex: 'base_url', width: 220 },
              {
                title: t('model.columns.apiKey'),
                dataIndex: 'api_key_configured',
                render: (value: boolean) => <StatusTag status={value} options={apiKeyOptions(t)} />
              },
              {
                title: t('model.columns.enabled'),
                dataIndex: 'enabled',
                render: (value: boolean, record: ModelItem) => (
                  <Switch
                    size="small"
                    checked={value}
                    loading={savingId === record.id}
                    onChange={() => void toggleEnabled(record)}
                  />
                )
              },
              {
                title: t('model.columns.lastTestStatus'),
                dataIndex: 'last_test_status',
                render: (value: ModelItem['last_test_status']) => (
                  <StatusTag status={value} options={testStatusOptions(t)} />
                )
              },
              { title: t('model.columns.revision'), dataIndex: 'revision' },
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
                    <ConfirmAction danger title={t('model.confirmDelete')} onConfirm={() => void remove(record)}>
                      {t('model.actions.delete')}
                    </ConfirmAction>
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
        )}
      </PageSection>
      <ModelFormModal
        visible={formVisible}
        model={formModel}
        onCancel={() => {
          setFormVisible(false);
          setFormModel(null);
        }}
        onSaved={(saved) => {
          setFormVisible(false);
          setFormModel(null);
          void refreshDetail(saved.id);
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
        labels={testLabels}
        onCancel={() => setTestVisible(false)}
      />
    </>
  );
}
