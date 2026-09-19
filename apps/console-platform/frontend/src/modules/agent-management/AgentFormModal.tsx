import { Banner, Form, Toast } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

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
    formApi.current?.setValues({ enabled: 'true' });
    if (props.agent) {
      formApi.current?.setValues({
        name: props.agent.name,
        description: props.agent.description ?? '',
        instructions: props.agent.instructions,
        model_id: props.agent.model_id,
        enabled: props.agent.enabled ? 'true' : 'false'
      });
    }
  }, [props.visible, props.agent]);

  const submit = async (values: FormValues): Promise<void> => {
    // eslint-disable-next-line no-console
    console.log('AGENT SUBMIT', JSON.stringify(values));
    setSaving(true);
    try {
      if (props.agent) {
        await updateAgent(props.agent.id, {
          expected_revision: props.agent.revision,
          name: values.name,
          description: values.description || undefined,
          instructions: values.instructions,
          model_id: values.model_id,
          enabled: values.enabled === 'true'
        });
      } else {
        await createAgent(
          {
            name: values.name,
            key: values.key,
            description: values.description || undefined,
            instructions: values.instructions,
            model_id: values.model_id,
            enabled: values.enabled === 'true'
          },
          crypto.randomUUID()
        );
      }
      props.onSaved();
    } catch {
      // [E-09] key 冲突/校验错误由 ApiClient Toast；Modal 保留，本地表单不被覆盖
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
      onOk={() => {
        formApi.current
          ?.validate()
          .then((values) => {
            // eslint-disable-next-line no-console
            console.log('VALIDATE OK', JSON.stringify(values).slice(0, 200));
            formApi.current?.submitForm();
          })
          .catch((errors) => {
            // eslint-disable-next-line no-console
            console.log('VALIDATE ERR', JSON.stringify(errors).slice(0, 300));
          });
      }}
      onCancel={props.onCancel}
      getFormApi={(api) => {
        formApi.current = api;
      }}
      onSubmit={(values) => {
        void submit(values as unknown as FormValues);
      }}
    >
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
