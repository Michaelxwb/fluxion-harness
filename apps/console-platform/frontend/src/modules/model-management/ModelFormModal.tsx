import { Banner } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import { ModelFields } from './ModelFields';
import { readOutputBudget } from './services/modelDeployments';
import {
  ModelInputError,
  buildModelCreateInput,
  buildModelUpdateInput,
  createModel,
  modelSaveErrorKey,
  updateModel,
  type ModelFormValues,
  type ModelInputReason,
  type ModelItem
} from './services/models';

/**
 * 逻辑模型新增/编辑（design frontend §3.2/§3.7；backend API-13）。
 *
 * - **只声明协议/型号/供方/参数**：连接与密钥不在本表单（唯一权威是物理凭据，TASK-086）；
 * - **输出预算必填**：`params.max_tokens` 是真正发送的上限，缺失/非法显式落到字段文案；
 * - **编辑回显只认真正发送的键**：旧行缺 `max_tokens` 时显式留空要求补填，不伪造默认预算；
 * - 保存失败（标识重复/版本冲突/输入边界）在表单内显式提示，保留表单供重试。
 */

const INPUT_ERROR_KEYS: Record<ModelInputReason, string> = {
  KEY_REQUIRED: 'model.form.key',
  NAME_REQUIRED: 'model.form.name',
  MODEL_ID_REQUIRED: 'model.form.modelId',
  MAX_TOKENS_INVALID: 'model.form.maxTokensInvalid'
};

interface FormValues {
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  provider_key: string;
  max_tokens?: number;
  enabled?: boolean;
}

function toFormValues(values: FormValues): ModelFormValues {
  return {
    key: values.key ?? '',
    name: values.name ?? '',
    protocol: 'OPENAI',
    model_id: values.model_id ?? '',
    provider_key: values.provider_key ?? '',
    max_tokens: values.max_tokens as number,
    enabled: values.enabled ?? true
  };
}

export interface ModelFormModalProps {
  visible: boolean;
  model: ModelItem | null;
  onCancel(): void;
  onSaved(model: ModelItem): void;
}

export function ModelFormModal(props: ModelFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    setErrorKey(null);
    formApi.current?.reset();
    formApi.current?.setValues({
      protocol: 'OPENAI',
      provider_key: '',
      enabled: true,
      max_tokens: undefined
    });
    if (props.model) {
      formApi.current?.setValues({
        key: props.model.key,
        name: props.model.name,
        protocol: 'OPENAI',
        model_id: props.model.model_id,
        provider_key: props.model.provider_key,
        // 旧行缺 params.max_tokens 时留空：由用户显式补填，不伪造默认预算
        max_tokens: readOutputBudget(props.model.params) ?? undefined,
        enabled: props.model.enabled
      });
    }
  }, [props.visible, props.model]);

  const submit = async (values: FormValues): Promise<void> => {
    setSaving(true);
    setErrorKey(null);
    const formValues = toFormValues(values);
    try {
      const saved = props.model
        ? await updateModel(props.model.id, buildModelUpdateInput(props.model, formValues))
        : await createModel(buildModelCreateInput(formValues));
      props.onSaved(saved);
    } catch (error) {
      // 输入边界与冲突码都落到表单内提示；表单保持打开供用户修正后重试
      setErrorKey(
        error instanceof ModelInputError
          ? INPUT_ERROR_KEYS[error.reason]
          : modelSaveErrorKey(error)
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={720}
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
      <ModelFields keyDisabled={props.model !== null} />
      {errorKey === null ? null : <Banner type="danger" closeIcon={null} description={t(errorKey)} />}
    </FormModal>
  );
}
