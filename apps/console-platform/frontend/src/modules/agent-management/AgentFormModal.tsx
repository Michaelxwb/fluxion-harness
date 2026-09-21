import { Banner, Form, Toast } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody, newRequestId } from '../../api/client';
import { FormModal } from '../../components/common/FormModal';
import { listModels } from '../model-management/services/models';
import { createAgent, updateAgent, type AgentDetail } from './services/agents';

export interface AgentFormModalProps {
  visible: boolean;
  agent: AgentDetail | null;
  onCancel(): void;
  onSaved(): void;
}

interface FormValues {
  name: string;
  key?: string;
  description?: string;
  instructions: string;
  model_id: string;
  enabled: 'true' | 'false';
}

interface ModelOption {
  value: string;
  label: string;
}

export function AgentFormModal(props: AgentFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    listModels({ page: 1, page_size: 100, enabled: 'true' })
      .then((page) => {
        setModels(page.items.map((model) => ({ value: model.id, label: `${model.name}（${model.model_id}）` })));
        // 创建模式默认选中第一个启用模型（异步列表无法用 initValue）
        if (!props.agent && page.items.length) {
          formApi.current?.setValue('model_id', page.items[0].id);
        }
      })
      .catch(() => {
        // 模型加载失败由 ApiClient 提示
      });
  }, [props.visible, props.agent]);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    setFormError(null);
    formApi.current?.setValues({ enabled: 'true' });
    if (props.agent) {
      formApi.current?.setValues({
        name: props.agent.name,
        key: props.agent.key,
        description: props.agent.description ?? '',
        instructions: props.agent.instructions,
        model_id: props.agent.model_id,
        enabled: props.agent.enabled ? 'true' : 'false'
      });
    }
  }, [props.visible, props.agent]);

  const submit = async (values: FormValues): Promise<void> => {
    setSaving(true);
    setFormError(null);
    try {
      if (props.agent) {
        await updateAgent(props.agent.id, {
          expected_revision: props.agent.revision,
          name: values.name,
          description: values.description ?? '',
          instructions: values.instructions,
          model_id: values.model_id,
          enabled: values.enabled === 'true'
        });
      } else {
        await createAgent(
          {
            name: values.name,
            key: values.key,
            description: values.description ?? '',
            instructions: values.instructions,
            model_id: values.model_id,
            enabled: values.enabled === 'true'
          },
          newRequestId()
        );
      }
      props.onSaved();
    } catch (error) {
      const body = apiErrorBody(error);
      if (body?.code === 'AGENT_KEY_EXISTS') {
        formApi.current?.setError('key', body.msg);
      } else if (body?.code === 'REVISION_CONFLICT') {
        setFormError(t('agent.form.revisionConflict'));
      } else if (body?.msg) {
        setFormError(body.msg);
      } else {
        setFormError(t('common.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={720}
      title={props.agent ? t('agent.form.editTitle') : t('agent.form.createTitle')}
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
      {formError ? (
        <Banner
          className="form-field-banner"
          type="danger"
          closeIcon={null}
          description={formError}
        />
      ) : null}
      <Banner
        className="form-field-banner"
        type="info"
        closeIcon={null}
        description={t('agent.form.banner')}
      />
      <div className="form-grid">
        <Form.Input
          field="name"
          label={t('agent.form.name')}
          rules={[{ required: true, message: t('agent.form.name') }]}
        />
        <Form.Input
          field="key"
          label={t('agent.form.key')}
          disabled={props.agent !== null}
          rules={props.agent ? [] : [{ required: true, message: t('agent.form.key') }]}
        />
        <Form.Select
          field="model_id"
          label={t('agent.form.model')}
          filter
          optionList={models}
          rules={[{ required: true, message: t('agent.form.model') }]}
          extraText={t('agent.form.modelHint')}
        />
        <Form.Select
          field="enabled"
          label={t('agent.columns.enabled')}
          optionList={[
            { value: 'true', label: t('common.status.enabled') },
            { value: 'false', label: t('common.status.disabled') }
          ]}
        />
        <div className="form-field-full">
          <Form.TextArea
            field="instructions"
            label={t('agent.form.instructions')}
            rows={5}
            rules={[{ required: true, message: t('agent.form.instructions') }]}
          />
        </div>
        <div className="form-field-full">
          <Form.TextArea field="description" label={t('agent.form.description')} rows={2} />
        </div>
      </div>
    </FormModal>
  );
}
