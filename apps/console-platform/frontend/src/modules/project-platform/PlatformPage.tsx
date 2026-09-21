import { Button, Input, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { RemoteTable } from '../../components/common/RemoteTable';
import { PlatformDetailSideSheet } from './PlatformDetailSideSheet';
import { PlatformTestModal } from './PlatformTestModal';
import { ProjectPlatformForm } from './ProjectPlatformForm';
import {
  deletePlatform,
  getAdapters,
  listPlatforms,
  type AdapterMetadata,
  type PlatformItem
} from './services/platforms';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', adapter_key: '', enabled: 'ALL' };

export function PlatformPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [keywordInput, setKeywordInput] = useState('');
  const [items, setItems] = useState<PlatformItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [adapters, setAdapters] = useState<AdapterMetadata[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailVersion, setDetailVersion] = useState(0);
  const [testTarget, setTestTarget] = useState<string | null>(null);
  const [reconfigureRequired, setReconfigureRequired] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [formPlatform, setFormPlatform] = useState<PlatformItem | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setParams((prev) => (prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }));
    }, 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listPlatforms({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        adapter_key: params.adapter_key || undefined,
        enabled: params.enabled === 'ALL' ? undefined : params.enabled === 'true'
      });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    getAdapters()
      .then((page) => setAdapters(page.items))
      .catch(() => setAdapters([]));
  }, []);

  const remove = async (platform: PlatformItem): Promise<void> => {
    try {
      await deletePlatform(platform.platform_id);
    } catch {
      return;
    }
    setDetailId(null);
    setReconfigureRequired(false);
    if (items.length === 1 && params.page > 1) {
      setParams((prev) => ({ ...prev, page: prev.page - 1 }));
    } else {
      await reload();
    }
  };

  return (
    <>
      <PageHeader title={t('platform.title')} description={t('platform.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button
              theme="solid"
              data-testid="create-platform"
              onClick={() => {
                setFormPlatform(null);
                setFormVisible(true);
              }}
            >
              {t('platform.add')}
            </Button>
          }
          search={
            <>
              <Input
                value={keywordInput}
                placeholder={t('platform.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={setKeywordInput}
              />
              <Select
                value={params.adapter_key || undefined}
                style={{ width: 160 }}
                showClear
                placeholder={t('platform.columns.adapter')}
                optionList={adapters.map((adapter) => ({ value: adapter.key, label: adapter.name }))}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, adapter_key: value ? String(value) : '', page: 1 }))
                }
              />
              <Select
                value={params.enabled}
                style={{ width: 130 }}
                optionList={[
                  { value: 'ALL', label: t('platform.filter.allEnabled') },
                  { value: 'true', label: t('common.status.enabled') },
                  { value: 'false', label: t('common.status.disabled') }
                ]}
                onChange={(value) => setParams((prev) => ({ ...prev, enabled: String(value), page: 1 }))}
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<PlatformItem>
          rowKey="platform_id"
          loading={loading}
          dataSource={items}
          page={params.page}
          pageSize={params.page_size}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(pageSize) => setParams((prev) => ({ ...prev, page_size: pageSize, page: 1 }))}
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('platform.empty')} />
            )
          }
          columns={[
            {
              title: t('platform.columns.name'),
              dataIndex: 'name',
              render: (value: string, record: PlatformItem) => (
                <Button
                  theme="borderless"
                  data-testid={`platform-link-${record.key}`}
                  onClick={() => {
                    setReconfigureRequired(false);
                    setDetailId(record.platform_id);
                    setDetailVersion((prev) => prev + 1);
                  }}
                >
                  {value}
                </Button>
              )
            },
            { title: t('platform.columns.key'), dataIndex: 'key' },
            { title: t('platform.columns.adapter'), dataIndex: 'adapter_key' },
            {
              title: t('platform.columns.resolverType'),
              dataIndex: 'resolver_type',
              render: (value: PlatformItem['resolver_type']) => t(`platform.resolverType.${value}`)
            },
            {
              title: t('platform.columns.credentialMode'),
              dataIndex: 'credential_mode',
              render: (value: PlatformItem['credential_mode']) => t(`platform.credentialMode.${value}`)
            },
            { title: t('platform.columns.configuredCredentials'), dataIndex: 'configured_user_credential_count' },
            {
              title: t('platform.columns.sharedCredential'),
              dataIndex: 'has_shared_credential',
              render: (value: boolean) =>
                value ? (
                  <Tag color="green">{t('platform.credentials.configured')}</Tag>
                ) : (
                  <Tag color="grey">{t('platform.credentials.notConfigured')}</Tag>
                )
            },
            {
              title: t('platform.columns.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean) => (
                <Tag color={value ? 'green' : 'grey'}>
                  {t(value ? 'common.status.enabled' : 'common.status.disabled')}
                </Tag>
              )
            },
            {
              title: t('platform.columns.updateTime'),
              dataIndex: 'update_time',
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              title: t('platform.columns.action'),
              render: (_: unknown, record: PlatformItem) => (
                <Button theme="borderless" onClick={() => setTestTarget(record.platform_id)}>
                  {t('platform.actions.test')}
                </Button>
              )
            }
          ]}
        />
      </PageSection>
      <PlatformDetailSideSheet
        key={`${detailId ?? 'none'}-${detailVersion}`}
        platformId={detailId}
        reconfigureRequired={reconfigureRequired}
        onClose={() => {
          setDetailId(null);
          setReconfigureRequired(false);
        }}
        onEdit={(platform) => {
          setFormPlatform(platform);
          setFormVisible(true);
        }}
        onDelete={(platform) => void remove(platform)}
        onChanged={() => void reload()}
      />
      <ProjectPlatformForm
        visible={formVisible}
        platform={formPlatform}
        onCancel={() => setFormVisible(false)}
        onSaved={(platformId, required) => {
          setFormVisible(false);
          setReconfigureRequired(required);
          setDetailId(platformId);
          setDetailVersion((prev) => prev + 1);
          void reload();
        }}
      />
      <PlatformTestModal
        visible={testTarget !== null}
        platformId={testTarget}
        onCancel={() => setTestTarget(null)}
      />
    </>
  );
}
