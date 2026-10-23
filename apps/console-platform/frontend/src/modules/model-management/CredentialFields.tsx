import { Form } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import type {
  ModelCredentialItem,
  ModelQuotaGroupOption
} from './services/modelCredentials';

/**
 * 物理 Key 字段面（资源池弹窗与新增模型向导共用）。
 *
 * 只负责写入字段：密钥仅写入、读取面只有 `configured`；编辑时留空表示保留原值。
 * `providerLocked` 预留给“由已有资源决定供方”的场景，避免复制一份字段实现。
 */
export interface CredentialFieldsProps {
  credential: ModelCredentialItem | null;
  quotaGroups: ModelQuotaGroupOption[];
  providerLocked?: boolean;
}

export function CredentialFields(props: CredentialFieldsProps) {
  const { t } = useTranslation();
  const { credential } = props;
  return (
    <div className="form-grid">
      <Form.Input
        field="key"
        label={t('model.credential.form.key')}
        disabled={credential !== null}
        rules={
          credential === null
            ? [{ required: true, message: t('model.credential.form.key') }]
            : []
        }
      />
      <Form.Input
        field="provider_key"
        label={t('model.credential.form.provider')}
        disabled={props.providerLocked}
        extraText={t('model.credential.form.providerHint')}
        rules={[{ required: true, message: t('model.credential.form.provider') }]}
      />
      <div className="form-field-full">
        <Form.Input
          field="base_url"
          label={t('model.credential.form.baseUrl')}
          extraText={t('model.credential.form.baseUrlHint')}
          rules={[
            { required: true, message: t('model.credential.form.baseUrl') },
            {
              pattern: /^https?:\/\/[^@\s#]+$/i,
              message: t('model.credential.form.baseUrlInvalid')
            }
          ]}
        />
      </div>
      <div className="form-field-full">
        <Form.Input
          field="api_key"
          label={t('model.credential.form.apiKey')}
          extraText={
            <>
              {t('model.credential.form.apiKeyHint')}
              {credential === null ? '' : ` ${t('model.credential.form.apiKeyKeep')}`}
              <br />
              {t('model.credential.form.apiKeyNotice')}
            </>
          }
          rules={
            credential === null
              ? [{ required: true, message: t('model.credential.form.apiKeyRequired') }]
              : []
          }
        />
      </div>
      <Form.Select
        field="quota_group_id"
        label={t('model.credential.form.quotaGroup')}
        showClear
        placeholder={t('model.credential.quota.exclusive')}
        extraText={t('model.credential.form.quotaGroupHint')}
        optionList={props.quotaGroups.map((group) => ({ value: group.id, label: group.key }))}
      />
      <Form.InputNumber
        field="max_inflight"
        label={t('model.credential.form.maxInflight')}
        min={1}
        precision={0}
        style={{ width: '100%' }}
        extraText={t('model.credential.form.maxInflightHint')}
        rules={[{ required: true, message: t('model.credential.form.maxInflightInvalid') }]}
      />
      <Form.Switch field="enabled" label={t('model.credential.form.enabled')} initValue />
    </div>
  );
}
