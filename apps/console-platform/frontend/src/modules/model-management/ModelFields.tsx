import { Form } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

/**
 * 逻辑模型字段面（新增向导与编辑弹窗共用）。
 *
 * 只声明协议/型号/供方/参数：连接与密钥的权威是物理凭据，本组件不持有任何连接/密钥字段。
 * `providerLocked` 用于新增向导：供方由所选物理 Key 决定，避免用户填出必然
 * `MODEL_PROVIDER_MISMATCH` 的组合。
 */
export interface ModelFieldsProps {
  /** 编辑时标识不可变更；新增时由用户填写。 */
  keyDisabled?: boolean;
  /** 供方由物理 Key 锁定（新增向导）。 */
  providerLocked?: boolean;
  /** 供方字段说明；锁定时换成“由所选物理 Key 决定”。 */
  providerHintKey?: string;
}

export function ModelFields(props: ModelFieldsProps) {
  const { t } = useTranslation();
  return (
    <div className="form-grid">
      <Form.Input
        field="name"
        label={t('model.form.name')}
        rules={[{ required: true, message: t('model.form.name') }]}
      />
      <Form.Input
        field="key"
        label={t('model.form.key')}
        disabled={props.keyDisabled}
        rules={props.keyDisabled ? [] : [{ required: true, message: t('model.form.key') }]}
      />
      <Form.Select
        field="protocol"
        label={t('model.form.protocol')}
        disabled
        initValue="OPENAI"
        extraText={t('model.form.protocolHint')}
        optionList={[{ value: 'OPENAI', label: 'OpenAI' }]}
      />
      <Form.Input
        field="model_id"
        label={t('model.form.modelId')}
        rules={[{ required: true, message: t('model.form.modelId') }]}
      />
      <div className="form-field-full">
        <Form.Input
          field="provider_key"
          label={t('model.form.provider')}
          disabled={props.providerLocked}
          extraText={t(props.providerHintKey ?? 'model.form.providerHint')}
        />
      </div>
      <div className="form-field-full">
        <Form.InputNumber
          field="max_tokens"
          label={t('model.form.maxTokens')}
          min={1}
          precision={0}
          style={{ width: '100%' }}
          extraText={t('model.form.maxTokensHint')}
          rules={[{ required: true, message: t('model.form.maxTokensInvalid') }]}
        />
      </div>
      <Form.Switch field="enabled" label={t('model.form.enabled')} initValue />
    </div>
  );
}
