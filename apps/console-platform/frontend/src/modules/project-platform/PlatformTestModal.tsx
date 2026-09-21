import { Banner, Descriptions, Modal, Spin, Tag } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody } from '../../api/client';
import { DateTimeText } from '../../components/common/DateTimeText';
import { testPlatform, type PlatformTestResult } from './services/platforms';

export interface PlatformTestModalProps {
  visible: boolean;
  platformId: string | null;
  onCancel(): void;
}

const CREDENTIAL_STATUS_KEYS: Record<string, string> = {
  ACTIVE: 'platform.test.credentialStatus.ACTIVE',
  INVALID: 'platform.test.credentialStatus.INVALID',
  MISSING: 'platform.test.credentialStatus.MISSING',
  NOT_CHECKED: 'platform.test.credentialStatus.NOT_CHECKED'
};

const ERROR_KEYS: Record<string, string> = {
  CREDENTIAL_MISSING: 'platform.test.credentialMissing',
  COMMON_VALIDATION_ERROR: 'platform.test.invalidConfig',
  PLATFORM_ADAPTER_NOT_FOUND: 'platform.test.adapterMissing'
};

export function PlatformTestModal(props: PlatformTestModalProps) {
  const { t } = useTranslation();
  const [result, setResult] = useState<PlatformTestResult | null>(null);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  useEffect(() => {
    if (!props.visible || !props.platformId) {
      return;
    }
    let cancelled = false;
    setResult(null);
    setErrorKey(null);
    testPlatform(props.platformId)
      .then((value) => {
        if (!cancelled) {
          setResult(value);
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        const body = apiErrorBody(error);
        setErrorKey(
          (body?.code ? ERROR_KEYS[body.code] : undefined) ?? 'platform.test.requestFailed'
        );
      });
    return () => {
      cancelled = true;
    };
  }, [props.visible, props.platformId, t]);

  const target =
    typeof result?.details.host === 'string'
      ? `${result.details.host}${result.details.port ? `:${result.details.port}` : ''}`
      : typeof result?.details.service_name === 'string'
        ? result.details.service_name
        : '-';
  const failureReason =
    result && typeof result.details.error === 'string' ? result.details.error : null;

  return (
    <Modal
      visible={props.visible}
      title={t('platform.test.title')}
      footer={null}
      width={620}
      onCancel={props.onCancel}
    >
      {errorKey ? (
        <Banner type="danger" closeIcon={null} description={t(errorKey)} />
      ) : result === null ? (
        <Spin style={{ display: 'block', margin: '16px auto' }} />
      ) : (
        <Descriptions
          row
          data={[
            {
              key: t('platform.test.configValid'),
              value: (
                <Tag color={result.config_valid ? 'green' : 'red'}>
                  {t(result.config_valid ? 'platform.test.passed' : 'platform.test.failed')}
                </Tag>
              )
            },
            {
              key: t('platform.test.connectivity'),
              value: (
                <Tag color={result.connectivity === 'REACHABLE' ? 'green' : 'red'}>
                  {t(`platform.test.connectivityValue.${result.connectivity}`)}
                </Tag>
              )
            },
            {
              key: t('platform.test.credentialRefStatus'),
              value: t(
                CREDENTIAL_STATUS_KEYS[result.credential_ref_status] ??
                  'platform.test.credentialStatus.NOT_CHECKED'
              )
            },
            {
              key: t('platform.form.adapter'),
              value: `${result.adapter_key} · ${result.adapter_version}`
            },
            { key: t('platform.test.target'), value: target },
            {
              key: t('platform.test.checkedAt'),
              value: <DateTimeText value={result.checked_at} />
            },
            ...(failureReason
              ? [
                  {
                    key: t('platform.test.failureReason'),
                    value: t(`platform.test.error.${failureReason}`, { defaultValue: failureReason })
                  }
                ]
              : [])
          ]}
        />
      )}
      <div className="detail-hint">{t('platform.test.hint')}</div>
    </Modal>
  );
}
