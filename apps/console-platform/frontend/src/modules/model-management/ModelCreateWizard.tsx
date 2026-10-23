import { Banner, Button, Form, Modal, Radio, RadioGroup, Select, Steps } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { CredentialFields } from './CredentialFields';
import { ModelFields } from './ModelFields';
import {
  ModelCredentialInputError,
  buildCredentialCreateInput,
  createCredential,
  credentialSaveErrorKey,
  listCredentials,
  listQuotaGroups,
  type ModelCredentialFormValues,
  type ModelCredentialInputReason,
  type ModelCredentialItem,
  type ModelQuotaGroupOption
} from './services/modelCredentials';
import { deploymentSaveErrorKey } from './services/modelDeployments';
import {
  MemberBindingFailedError,
  bindFirstMember,
  createModelWithMember,
  isMemberAlreadyBound
} from './services/modelOnboarding';
import {
  ModelInputError,
  modelSaveErrorKey,
  type ModelFormValues,
  type ModelInputReason,
  type ModelItem
} from './services/models';

/**
 * 新增模型两步向导（design frontend §3.2/§3.4；backend API-01/API-13/API-16）。
 *
 * 第一步先解决“模型能不能调用”的前置事实：选择一把已配置的物理 Key，或就地新建一把；
 * 第二步只填逻辑模型字段，保存时自动把第一步的 Key 绑成第一条成员（成员名取 Key 标识，权重 1）。
 *
 * - **供方锁定**：模型的 `provider_key` 取所选 Key 的供方，避免同供方核对必然失败的组合；
 * - **绑定失败不丢模型**：模型已创建但成员绑定失败时，保留模型并给出“重试绑定 / 去成员页”；
 * - **创建 Key 是第一步的显式副作用**：点击“下一步”即先保存 Key，取消向导不会回滚它；
 *   文案里明确提示，避免用户以为整单是原子的。
 */

const CREDENTIAL_PAGE_SIZE = 100;

const CREDENTIAL_INPUT_ERROR_KEYS: Record<ModelCredentialInputReason, string> = {
  API_KEY_REQUIRED: 'model.credential.form.apiKeyRequired',
  MAX_INFLIGHT_INVALID: 'model.credential.form.maxInflightInvalid'
};

const MODEL_INPUT_ERROR_KEYS: Record<ModelInputReason, string> = {
  KEY_REQUIRED: 'model.form.key',
  NAME_REQUIRED: 'model.form.name',
  MODEL_ID_REQUIRED: 'model.form.modelId',
  MAX_TOKENS_INVALID: 'model.form.maxTokensInvalid'
};

interface CredentialFormValues {
  key: string;
  provider_key: string;
  base_url: string;
  api_key: string;
  quota_group_id?: string | null;
  max_inflight: number;
  enabled: boolean;
}

interface ModelFormValuesRaw {
  key: string;
  name: string;
  protocol: string;
  model_id: string;
  provider_key: string;
  max_tokens?: number;
  enabled?: boolean;
}

function toCredentialValues(raw: CredentialFormValues): ModelCredentialFormValues {
  return {
    key: raw.key ?? '',
    provider_key: raw.provider_key ?? '',
    base_url: raw.base_url ?? '',
    api_key: raw.api_key ?? '',
    quota_group_id: raw.quota_group_id ?? null,
    max_inflight: Number(raw.max_inflight),
    enabled: raw.enabled ?? true
  };
}

function toModelValues(raw: ModelFormValuesRaw): ModelFormValues {
  return {
    key: raw.key ?? '',
    name: raw.name ?? '',
    protocol: 'OPENAI',
    model_id: raw.model_id ?? '',
    provider_key: raw.provider_key ?? '',
    max_tokens: raw.max_tokens as number,
    enabled: raw.enabled ?? true
  };
}

export interface ModelCreateWizardProps {
  visible: boolean;
  onCancel(): void;
  onCreated(model: ModelItem): void;
  /** 模型已创建但成员绑定失败时刷新列表（不关闭向导）。 */
  onRefresh?(): void;
}

export function ModelCreateWizard(props: ModelCreateWizardProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<'existing' | 'new'>('existing');
  const [credentials, setCredentials] = useState<ModelCredentialItem[]>([]);
  const [credentialsTotal, setCredentialsTotal] = useState(0);
  const [credentialsFailed, setCredentialsFailed] = useState(false);
  const [credentialsLoaded, setCredentialsLoaded] = useState(false);
  const [credentialsLoading, setCredentialsLoading] = useState(false);
  const [quotaGroups, setQuotaGroups] = useState<ModelQuotaGroupOption[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [credential, setCredential] = useState<ModelCredentialItem | null>(null);
  const [modelDraft, setModelDraft] = useState<ModelFormValuesRaw | null>(null);
  const [savingKey, setSavingKey] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [createdModel, setCreatedModel] = useState<ModelItem | null>(null);
  const [bindingFailed, setBindingFailed] = useState(false);
  const credentialFormApi = useRef<FormApi | null>(null);
  const modelFormApi = useRef<FormApi | null>(null);
  const credentialRequestId = useRef('');

  const eligibleCredentials = useMemo(
    () =>
      credentials.filter(
        (item) => item.enabled && item.configured && item.provider_key.trim() !== ''
      ),
    [credentials]
  );

  const loadCredentials = useCallback(async (): Promise<void> => {
    setCredentialsLoading(true);
    setCredentialsLoaded(false);
    setCredentialsFailed(false);
    try {
      const page = await listCredentials({ page: 1, page_size: CREDENTIAL_PAGE_SIZE });
      setCredentials(page.items);
      setCredentialsTotal(page.total);
    } catch {
      setCredentialsFailed(true);
      setCredentials([]);
      setCredentialsTotal(0);
    } finally {
      setCredentialsLoaded(true);
      setCredentialsLoading(false);
    }
  }, []);

  const loadQuotaGroups = useCallback(async (): Promise<void> => {
    try {
      const page = await listQuotaGroups();
      setQuotaGroups(page.items);
    } catch {
      setQuotaGroups([]);
    }
  }, []);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    setStep(0);
    setMode('existing');
    setSelectedId('');
    setCredential(null);
    setModelDraft(null);
    setErrorKey(null);
    setErrorText(null);
    setCreatedModel(null);
    setBindingFailed(false);
    credentialRequestId.current = newRequestId();
    credentialFormApi.current?.reset();
    credentialFormApi.current?.setValues({ max_inflight: 1, enabled: true });
    void loadCredentials();
    void loadQuotaGroups();
  }, [props.visible, loadCredentials, loadQuotaGroups]);

  useEffect(() => {
    if (!props.visible || !credentialsLoaded || step !== 0 || mode !== 'existing') {
      return;
    }
    if (eligibleCredentials.length === 0) {
      setMode('new');
      return;
    }
    if (eligibleCredentials.length === 1 && !selectedId) {
      setSelectedId(eligibleCredentials[0].id);
    }
  }, [props.visible, credentialsLoaded, step, mode, eligibleCredentials, selectedId]);

  const clearError = (): void => {
    setErrorKey(null);
    setErrorText(null);
  };

  const selectedCredential = eligibleCredentials.find((item) => item.id === selectedId) ?? null;

  const submitCredential = async (raw: CredentialFormValues): Promise<void> => {
    clearError();
    setSavingKey(true);
    try {
      const created = await createCredential(
        buildCredentialCreateInput(toCredentialValues(raw)),
        credentialRequestId.current
      );
      setCredential(created);
      setCredentials((items) => [created, ...items]);
      setCredentialsTotal((total) => total + 1);
      setSelectedId(created.id);
      setMode('existing');
      credentialFormApi.current?.setValue('api_key', '');
      credentialRequestId.current = newRequestId();
      setStep(1);
    } catch (error) {
      setErrorKey(
        error instanceof ModelCredentialInputError
          ? CREDENTIAL_INPUT_ERROR_KEYS[error.reason]
          : credentialSaveErrorKey(error)
      );
    } finally {
      setSavingKey(false);
    }
  };

  const nextFromCredential = (): void => {
    clearError();
    if (mode === 'existing') {
      if (selectedCredential === null) {
        setErrorKey('model.create.selectKeyRequired');
        return;
      }
      setCredential(selectedCredential);
      setStep(1);
      return;
    }
    credentialFormApi.current?.submitForm();
  };

  const submitModel = async (raw: ModelFormValuesRaw): Promise<void> => {
    clearError();
    if (credential === null) {
      setErrorKey('model.create.selectKeyRequired');
      setStep(0);
      return;
    }
    setSavingModel(true);
    try {
      const result = await createModelWithMember(toModelValues(raw), credential);
      props.onCreated(result.model);
    } catch (error) {
      if (error instanceof ModelInputError) {
        setErrorKey(MODEL_INPUT_ERROR_KEYS[error.reason]);
      } else if (error instanceof MemberBindingFailedError) {
        setCreatedModel(error.model);
        setBindingFailed(true);
        setErrorText(
          t('model.create.bindingFailed', {
            reason: t(deploymentSaveErrorKey(error.reason))
          })
        );
        props.onRefresh?.();
      } else {
        setErrorKey(modelSaveErrorKey(error));
      }
    } finally {
      setSavingModel(false);
    }
  };

  const retryBinding = async (): Promise<void> => {
    if (createdModel === null || credential === null) {
      return;
    }
    clearError();
    setSavingModel(true);
    try {
      await bindFirstMember(createdModel, credential);
      props.onCreated(createdModel);
    } catch (error) {
      if (isMemberAlreadyBound(error)) {
        props.onCreated(createdModel);
        return;
      }
      setErrorText(
        t('model.create.bindingFailed', {
          reason: t(deploymentSaveErrorKey(error))
        })
      );
    } finally {
      setSavingModel(false);
    }
  };

  const footer =
    step === 0 ? (
      <div className="model-wizard-footer">
        <Button type="primary" onClick={props.onCancel}>
          {t('common.cancel')}
        </Button>
        <Button
          data-testid="model-wizard-next"
          theme="solid"
          loading={savingKey}
          onClick={() => void nextFromCredential()}
        >
          {t('model.create.next')}
        </Button>
      </div>
    ) : (
      <div className="model-wizard-footer">
        {bindingFailed && createdModel !== null ? (
          <>
            <Button onClick={() => props.onCreated(createdModel)}>
              {t('model.create.goMembers')}
            </Button>
            <Button
              data-testid="model-wizard-retry"
              theme="solid"
              loading={savingModel}
              onClick={() => void retryBinding()}
            >
              {t('model.create.retryBind')}
            </Button>
          </>
        ) : (
          <>
            <Button
              data-testid="model-wizard-back"
              type="primary"
              disabled={savingModel}
              onClick={() => {
                clearError();
                setModelDraft(
                  (modelFormApi.current?.getValues() as ModelFormValuesRaw | undefined) ?? null
                );
                setStep(0);
              }}
            >
              {t('model.create.back')}
            </Button>
            <Button
              data-testid="model-wizard-submit"
              theme="solid"
              loading={savingModel}
              onClick={() => modelFormApi.current?.submitForm()}
            >
              {t('model.create.submit')}
            </Button>
          </>
        )}
      </div>
    );

  return (
    <Modal
      centered
      className="app-form-modal"
      visible={props.visible}
      title={t('model.create.title')}
      width={760}
      style={{ maxWidth: 'calc(100vw - 32px)' }}
      footer={footer}
      onCancel={props.onCancel}
      maskClosable={false}
    >
      {props.visible ? (
        <div data-testid="create-model-wizard">
          <Steps current={step} type="basic" size="small" className="model-wizard-steps">
            <Steps.Step title={t('model.create.step.key')} />
            <Steps.Step title={t('model.create.step.model')} />
          </Steps>

          {step === 0 ? (
            <Form
              key="credential-step"
              className="app-modal-form"
              labelPosition="top"
              initValues={{ max_inflight: 1, enabled: true }}
              getFormApi={(api) => {
                credentialFormApi.current = api;
              }}
              onSubmit={(values) => {
                void submitCredential(values as unknown as CredentialFormValues);
              }}
            >
              <div className="form-field-full">
                <RadioGroup
                  value={mode}
                  onChange={(event) => {
                    clearError();
                    setMode(event.target.value as 'existing' | 'new');
                  }}
                >
                  <Radio value="existing" disabled={eligibleCredentials.length === 0}>
                    {t('model.create.mode.existing')}
                  </Radio>
                  <Radio value="new">{t('model.create.mode.new')}</Radio>
                </RadioGroup>
              </div>

              {credentialsFailed ? (
                <div className="form-field-full">
                  <Banner
                    type="danger"
                    closeIcon={null}
                    description={t('model.create.credentialsFailed')}
                  />
                  <Button onClick={() => void loadCredentials()}>{t('common.retry')}</Button>
                </div>
              ) : null}

              {credentialsLoaded && !credentialsFailed && eligibleCredentials.length === 0 ? (
                <div className="form-field-full">
                  <Banner
                    type="warning"
                    closeIcon={null}
                    description={t('model.create.noEligibleKeys')}
                  />
                </div>
              ) : null}

              {mode === 'existing' ? (
                <div className="form-grid">
                  <div className="form-field-full">
                    <div className="form-section-title">{t('model.create.selectKey')}</div>
                    <Select
                      value={selectedId || undefined}
                      loading={credentialsLoading}
                      placeholder={t('model.create.selectKey')}
                      style={{ width: '100%' }}
                      optionList={eligibleCredentials.map((item) => ({
                        value: item.id,
                        label: `${item.key} · ${item.provider_key}`
                      }))}
                      onChange={(value) => {
                        clearError();
                        setSelectedId(String(value ?? ''));
                      }}
                    />
                  </div>
                  {selectedCredential === null ? null : (
                    <div className="form-field-full">
                      <Banner
                        type="info"
                        closeIcon={null}
                        description={`${selectedCredential.provider_key} · ${selectedCredential.base_url}`}
                      />
                    </div>
                  )}
                  {credentialsTotal > credentials.length ? (
                    <div className="form-field-full form-section-hint">
                      {t('model.create.credentialsTruncated', {
                        shown: credentials.length,
                        total: credentialsTotal
                      })}
                    </div>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="form-section-hint">{t('model.create.newKeyHint')}</div>
                  <CredentialFields credential={null} quotaGroups={quotaGroups} />
                </>
              )}
            </Form>
          ) : (
            <Form
              key="model-step"
              className="app-modal-form"
              labelPosition="top"
              initValues={{
                ...(modelDraft ?? {}),
                protocol: 'OPENAI',
                provider_key: credential?.provider_key ?? '',
                enabled: modelDraft?.enabled ?? true
              }}
              getFormApi={(api) => {
                modelFormApi.current = api;
              }}
              onSubmit={(values) => {
                void submitModel(values as unknown as ModelFormValuesRaw);
              }}
            >
              {credential === null ? null : (
                <div className="form-field-full">
                  <Banner
                    type="info"
                    closeIcon={null}
                    description={`${credential.key} · ${credential.provider_key} · ${credential.base_url}`}
                  />
                </div>
              )}
              <ModelFields
                providerLocked
                providerHintKey="model.create.providerLockedHint"
              />
            </Form>
          )}

          {errorText !== null ? (
            <Banner type="danger" closeIcon={null} description={errorText} />
          ) : errorKey !== null ? (
            <Banner type="danger" closeIcon={null} description={t(errorKey)} />
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
