import { Descriptions, Modal, Spin, Tag } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DateTimeText } from '../../components/common/DateTimeText';
import { testPlatform, type PlatformTestResult } from './services/platforms';

export interface PlatformTestModalProps {
  visible: boolean;
  platformId: string | null;
  onCancel(): void;
}

export function PlatformTestModal(props: PlatformTestModalProps) {
  const { t } = useTranslation();
  const [result, setResult] = useState<PlatformTestResult | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!props.visible || !props.platformId) {
      return;
    }
    setResult(null);
    setError(false);
    testPlatform(props.platformId)
      .then(setResult)
      .catch(() => setError(true));
  }, [props.visible, props.platformId]);

  return (
    <Modal
      visible={props.visible}
      title={t('platform.test.title')}
      footer={null}
      width={620}
      onCancel={props.onCancel}
    >
      {error ? (
        <Tag color="red">{t('platform.test.failed')}</Tag>
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
              value: result.credential_ref_status
            },
            { key: t('platform.form.adapter'), value: `${result.adapter_key} · ${result.adapter_version}` },
            {
              key: t('platform.test.checkedAt'),
              value: <DateTimeText value={result.checked_at} />
            },
            {
              key: t('platform.test.details'),
              value: Object.keys(result.details).length > 0 ? JSON.stringify(result.details) : '-'
            }
          ]}
        />
      )}
      <div className="detail-hint">{t('platform.test.hint')}</div>
    </Modal>
  );
}
