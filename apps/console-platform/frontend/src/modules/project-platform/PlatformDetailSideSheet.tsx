import { Banner, Button, Spin, Tabs, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { ErrorState } from '../../components/common/ErrorState';
import { getPlatform, type PlatformItem } from './services/platforms';
import { PlatformCredentialTab } from './PlatformCredentialTab';
import { PlatformTestModal } from './PlatformTestModal';

export interface PlatformDetailSideSheetProps {
  platformId: string | null;
  reconfigureRequired: boolean;
  onClose(): void;
  onEdit(platform: PlatformItem): void;
  onDelete(platform: PlatformItem): void;
  onChanged(): void;
}

export function PlatformDetailSideSheet(props: PlatformDetailSideSheetProps) {
  const { t } = useTranslation();
  const [platform, setPlatform] = useState<PlatformItem | null>(null);
  const [failed, setFailed] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [testVisible, setTestVisible] = useState(false);

  const load = useCallback(async () => {
    if (!props.platformId) {
      setPlatform(null);
      setFailed(false);
      return;
    }
    try {
      setPlatform(await getPlatform(props.platformId));
      setFailed(false);
    } catch {
      setPlatform(null);
      setFailed(true);
    }
  }, [props.platformId]);

  useEffect(() => {
    setActiveTab('basic');
    void load();
  }, [load]);

  useEffect(() => {
    if (props.reconfigureRequired) {
      setActiveTab('credentials');
    }
  }, [props.reconfigureRequired]);

  if (!props.platformId) {
    return null;
  }

  const resolverValue =
    platform === null
      ? '-'
      : platform.resolver_type === 'BASE_URL'
        ? String(platform.resolver_config.base_url ?? '-')
        : String(platform.resolver_config.service_name ?? '-');

  return (
    <DetailSideSheet
      visible
      title={platform?.name ?? t('platform.detail.title')}
      subtitle={platform?.key}
      activeTab={activeTab}
      onTabChange={setActiveTab}
      actions={
        platform ? (
          <>
            <Button theme="borderless" onClick={() => setTestVisible(true)}>
              {t('platform.actions.test')}
            </Button>
            <Button theme="solid" onClick={() => props.onEdit(platform)}>
              {t('platform.actions.edit')}
            </Button>
            <ConfirmAction danger title={t('platform.confirmDelete')} onConfirm={() => props.onDelete(platform)}>
              {t('platform.actions.delete')}
            </ConfirmAction>
          </>
        ) : null
      }
      onCancel={props.onClose}
      notice={
        props.reconfigureRequired ? (
          <Banner type="warning" description={t('platform.detail.reconfigureRequired')} />
        ) : null
      }
    >
      <Tabs.TabPane itemKey="basic" tab={t('platform.detail.basic')}>
        {failed ? (
          <ErrorState onRetry={() => void load()} />
        ) : platform === null ? (
          <Spin style={{ display: 'block', margin: '16px auto' }} />
        ) : (
          <>
            <div className="detail-section-title">{t('platform.detail.basic')}</div>
            <DetailGrid
              items={[
                { label: t('platform.form.name'), value: platform.name },
                { label: t('platform.form.key'), value: platform.key },
                { label: t('platform.columns.adapter'), value: platform.adapter_key },
                {
                  label: t('platform.form.resolverType'),
                  value: t(`platform.resolverType.${platform.resolver_type}`)
                },
                {
                  label: t('platform.form.resolverConfig'),
                  value: resolverValue
                },
                {
                  label: t('platform.form.credentialMode'),
                  value: t(`platform.credentialMode.${platform.credential_mode}`)
                },
                {
                  label: t('platform.columns.configuredCredentials'),
                  value: platform.configured_user_credential_count
                },
                {
                  label: t('platform.columns.sharedCredential'),
                  value: platform.has_shared_credential
                    ? t('platform.credentials.configured')
                    : t('platform.credentials.notConfigured')
                },
                {
                  label: t('platform.form.enabled'),
                  value: (
                    <Tag color={platform.enabled ? 'green' : 'grey'}>
                      {t(platform.enabled ? 'common.status.enabled' : 'common.status.disabled')}
                    </Tag>
                  )
                },
                { label: t('platform.columns.updateTime'), value: <DateTimeText value={platform.update_time} /> }
              ]}
            />
            <div className="detail-hint">{t('platform.detail.hint')}</div>
          </>
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="credentials" tab={t('platform.detail.credentials')}>
        {failed ? (
          <ErrorState onRetry={() => void load()} />
        ) : platform === null ? null : (
          <PlatformCredentialTab platform={platform} onChanged={props.onChanged} />
        )}
      </Tabs.TabPane>
      <PlatformTestModal
        visible={testVisible}
        platformId={platform?.platform_id ?? null}
        onCancel={() => setTestVisible(false)}
      />
    </DetailSideSheet>
  );
}
