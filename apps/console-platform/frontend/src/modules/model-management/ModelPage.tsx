import { actionColumn } from '../../components/common/tableLayout';
import { Button, Input, Select, Tabs, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../auth/AuthContext';
import { ListActionButton } from '../../components/common/ListActionButton';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { RemoteTable } from '../../components/common/RemoteTable';
import { TableText } from '../../components/common/TableText';
import { ModelCredentialPanel } from './ModelCredentialPanel';
import { ModelDetailSideSheet, ModelTestResultModal } from './ModelDetailSideSheet';
import { ModelFormModal } from './ModelFormModal';
import { ModelQuotaPanel } from './ModelQuotaPanel';
import { testStatusOptions } from './statusOptions';
import { ModelStatusIcons } from './ModelStatusIcons';
import {
  batchTestModels,
  deleteModel,
  getModel,
  listModels,
  updateModel,
  type ModelItem,
  type ModelTestResult
} from './services/models';

const DEFAULT_PARAMS = { page: 1, page_size: 15, keyword: '', enabled: 'ALL', last_test_status: '' };
const EMPTY_FILTERS = { keyword: '', enabled: 'ALL', last_test_status: '' };

export function ModelPage() {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
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
  const [resourcesVisible, setResourcesVisible] = useState(false);
  const [resourcesTab, setResourcesTab] = useState('credentials');
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

  /** 下拉类筛选：选中即生效。只提交本字段的补丁，不带上尚未回车的 keyword 草稿。 */
  const applyFilter = (patch: Partial<typeof EMPTY_FILTERS>): void => {
    setFilters((prev) => ({ ...prev, ...patch }));
    setParams((prev) => ({ ...prev, ...patch, page: 1 }));
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

  /** 资源池/成员变更后的唯一回流点：按服务端事实重读列表，不就地猜状态。 */
  const afterModelChanged = useCallback((): void => {
    void reload();
  }, [reload]);

  const toggleEnabled = async (model: ModelItem): Promise<void> => {
    setSavingId(model.id);
    try {
      // 单字段版本更新：只翻转 enabled（CAS），不重发连接/密钥等本面不持有的字段
      await updateModel(model.id, { expected_revision: model.revision, enabled: !model.enabled });
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
              {isAdmin ? (
                <Button
                  data-testid="open-model-resources"
                  onClick={() => {
                    setResourcesTab('credentials');
                    setResourcesVisible(true);
                  }}
                >
                  {t('model.resources.manage')}
                </Button>
              ) : null}
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
                onChange={(value) => applyFilter({ enabled: String(value) })}
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
                onChange={(value) => applyFilter({ last_test_status: value ? String(value) : '' })}
              />
              <ListActionButton data-testid="reset-model" onClick={resetFilters} action="reset" />
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        {failed ? (
          <ErrorState onRetry={() => void reload()} />
        ) : (
          <RemoteTable<ModelItem>
            className="model-table app-table-fit app-table-nowrap"
            scroll={{ x: '100%' }}
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
                width: 'auto',
                ellipsis: true,
                render: (value: string, record: ModelItem) => (
                  <EntityLink testId={`model-link-${value}`} onClick={() => void openDetail(record.id)}>
                    <TableText>{value}</TableText>
                  </EntityLink>
                )
              },
              { title: t('model.columns.name'), dataIndex: 'name', width: 'auto', ellipsis: true },
              { title: t('model.columns.modelId'), dataIndex: 'model_id', width: 'auto', ellipsis: true },
              { title: t('model.columns.protocol'), dataIndex: 'protocol', width: 88 },
              {
                title: t('model.columns.provider'),
                dataIndex: 'provider_key',
                width: 'auto',
                ellipsis: true,
                render: (value: string) =>
                  value === '' ? t('model.provider.missing') : value
              },
              {
                title: t('model.columns.statusSummary'),
                width: 108,
                render: (_: unknown, record: ModelItem) => (
                  <ModelStatusIcons
                    model={record}
                    saving={savingId === record.id}
                    onToggle={() => void toggleEnabled(record)}
                  />
                )
              },
              { title: t('model.columns.revision'), dataIndex: 'revision', width: 80 },
              {
                title: t('model.columns.updateTime'),
                dataIndex: 'update_time',
                width: 176,
                render: (value: string) => <DateTimeText value={value} />
              },
              {
                ...actionColumn(112),
                title: t('model.columns.actions'),
                render: (_: unknown, record: ModelItem) => (
                  <div className="model-table-actions">
                    <Button
                      theme="borderless"
                      onClick={() => {
                        setFormModel(record);
                        setFormVisible(true);
                      }}
                    >
                      {t('model.actions.edit')}
                    </Button>
                    <ConfirmAction title={t('model.confirmDelete')} onConfirm={() => void remove(record)}>
                      {t('model.actions.delete')}
                    </ConfirmAction>
                  </div>
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
        onChanged={afterModelChanged}
        onCancel={() => setDetail(null)}
      />
      {isAdmin && resourcesVisible ? (
        <DetailSideSheet
          visible
          title={t('model.resources.title')}
          subtitle={t('model.resources.subtitle')}
          activeTab={resourcesTab}
          onTabChange={(key) => setResourcesTab(String(key))}
          onCancel={() => setResourcesVisible(false)}
        >
          <Tabs.TabPane itemKey="credentials" tab={t('model.resources.credentialsTab')}>
            <ModelCredentialPanel onChanged={afterModelChanged} />
          </Tabs.TabPane>
          <Tabs.TabPane itemKey="quotas" tab={t('model.resources.quotasTab')}>
            <ModelQuotaPanel onChanged={afterModelChanged} />
          </Tabs.TabPane>
        </DetailSideSheet>
      ) : null}
      <ModelTestResultModal
        visible={testVisible}
        results={testResults}
        labels={testLabels}
        onCancel={() => setTestVisible(false)}
      />
    </>
  );
}
