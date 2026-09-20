import { Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { createModel, updateModel, type ModelItem, type ModelSaveInput } from './services/models';

export interface ModelFormModalProps {
  visible: boolean;
  model: ModelItem | null;
  onCancel(): void;
  onSaved(model: ModelItem): void;
}

interface FormValues {
  key: string;
  name: string;
  protocol: string;
  base_url: string;
  model_id: string;
  api_key?: string;
  enabled?: boolean;
}

export function ModelFormModal(props: ModelFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    formApi.current?.setValues({ protocol: 'OPENAI', enabled: true });
    if (props.model) {
      formApi.current?.setValues({
        key: props.model.key,
        name: props.model.name,
        protocol: 'OPENAI',
        base_url: props.model.base_url,
        model_id: props.model.model_id,
        api_key: '',
        enabled: props.model.enabled
      });
    }
  }, [props.visible, props.model]);

  const submit = async (values: FormValues): Promise<void> => {
    setSaving(true);
    const payload: ModelSaveInput = {
      name: values.name,
      base_url: values.base_url,
      model_id: values.model_id,
      enabled: values.enabled ?? true
    };
    if (values.api_key) {
      payload.api_key = values.api_key;
    }
    if (!props.model) {
      payload.key = values.key;
    }
    try {
      const saved = props.model
        ? await updateModel(props.model.id, { ...payload, expected_revision: props.model.revision })
        : await createModel(payload);
      props.onSaved(saved);
    } catch {
      // 统一由 ApiClient 展示本地化错误；REVISION_CONFLICT 时保留表单供重试
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={520}
      title={props.model ? t('model.form.editTitle') : t('model.form.createTitle')}
      okText={t('common.save')}
      confirmLoading={saving}
      onOk={() => formApi.current?.submitForm()}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as FormValues);
      }}
    >
      <Form.Input
        field="name"
        label={t('model.form.name')}
        rules={[{ required: true, message: t('model.form.name') }]}
      />
      <Form.Input
        field="key"
        label={t('model.form.key')}
        disabled={props.model !== null}
        rules={props.model ? [] : [{ required: true, message: t('model.form.key') }]}
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
      <Form.Input
        field="base_url"
        label={t('model.form.baseUrl')}
        extraText={t('model.form.baseUrlHint')}
        rules={[
          { required: true, message: t('model.form.baseUrl') },
          {
            pattern: /^https?:\/\/.+/,
            message: t('model.form.baseUrlInvalid')
          }
        ]}
      />
      <Form.Input
        field="api_key"
        label={t('model.form.apiKey')}
        extraText={
          <>
            {props.model
              ? `${t('model.form.apiKeyHint')} ${t('model.form.apiKeyKeep')}`
              : t('model.form.apiKeyHint')}
            <br />
            {t('model.form.apiKeyNotice')}
          </>
        }
      />
      <Form.Switch field="enabled" label={t('model.form.enabled')} initValue />
    </FormModal>
  );
}
