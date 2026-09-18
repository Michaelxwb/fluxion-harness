import { Banner, Button, Popconfirm, Spin, Tabs } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailGrid } from '../../components/common/DetailGrid';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
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
  const [activeTab, setActiveTab] = useState('basic');
  const [testVisible, setTestVisible] = useState(false);

  useEffect(() => {
    if (!props.platformId) {
      setPlatform(null);
      setActiveTab('basic');
      return;
    }
    let cancelled = false;
    getPlatform(props.platformId)
      .then((value) => {
        if (!cancelled) {
          setPlatform(value);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPlatform(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [props.platformId]);

  useEffect(() => {
    if (props.reconfigureRequired) {
      setActiveTab('credentials');
    }
  }, [props.reconfigureRequired]);

  if (!props.platformId) {
    return null;
  }

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
            <Popconfirm
              title={t('platform.confirmDelete')}
              onConfirm={() => props.onDelete(platform)}
            >
              <Button theme="borderless" type="danger">
                {t('platform.actions.delete')}
              </Button>
            </Popconfirm>
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
        {platform === null ? (
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
                  value: JSON.stringify(platform.resolver_config)
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
                { label: t('platform.form.enabled'), value: String(platform.enabled) },
                { label: t('platform.columns.updateTime'), value: <DateTimeText value={platform.update_time} /> }
              ]}
            />
            <div className="detail-hint">{t('platform.detail.hint')}</div>
          </>
        )}
      </Tabs.TabPane>
      <Tabs.TabPane itemKey="credentials" tab={t('platform.detail.credentials')}>
        {platform === null ? null : <PlatformCredentialTab platform={platform} />}
      </Tabs.TabPane>
      <PlatformTestModal
        visible={testVisible}
        platformId={platform?.platform_id ?? null}
        onCancel={() => setTestVisible(false)}
      />
    </DetailSideSheet>
  );
}
