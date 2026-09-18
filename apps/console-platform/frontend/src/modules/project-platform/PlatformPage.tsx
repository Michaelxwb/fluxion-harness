import { Button, Input, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { RemoteTable } from '../../components/common/RemoteTable';
import { PlatformDetailSideSheet } from './PlatformDetailSideSheet';
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
  const [items, setItems] = useState<PlatformItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [adapters, setAdapters] = useState<AdapterMetadata[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detailVersion, setDetailVersion] = useState(0);
  const [reconfigureRequired, setReconfigureRequired] = useState(false);
  const [formVisible, setFormVisible] = useState(false);
  const [formPlatform, setFormPlatform] = useState<PlatformItem | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listPlatforms({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        adapter_key: params.adapter_key || undefined,
        enabled: params.enabled === 'ALL' ? undefined : params.enabled === 'true'
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

  useEffect(() => {
    getAdapters()
      .then((page) => setAdapters(page.items))
      .catch(() => setAdapters([]));
  }, []);

  const remove = async (platform: PlatformItem): Promise<void> => {
    await deletePlatform(platform.platform_id);
    setDetailId(null);
    setReconfigureRequired(false);
    await reload();
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
                value={params.keyword}
                placeholder={t('platform.searchPlaceholder')}
                style={{ width: 200 }}
                onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
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
          empty={<EmptyState title={t('platform.empty')} />}
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
    </>
  );
}
