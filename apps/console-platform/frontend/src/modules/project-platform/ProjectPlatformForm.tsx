import { Form } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import {
  createPlatform,
  getAdapters,
  updatePlatform,
  type AdapterMetadata,
  type PlatformItem,
  type PlatformSaveInput
} from './services/platforms';

export interface ProjectPlatformFormProps {
  visible: boolean;
  platform: PlatformItem | null;
  onCancel(): void;
  onSaved(platformId: string, reconfigureRequired: boolean): void;
}

interface FormValues {
  name: string;
  key: string;
  resolver_type: 'BASE_URL' | 'SERVICE_DISCOVERY';
  base_url?: string;
  service_name?: string;
  adapter_key: string;
  credential_mode: PlatformItem['credential_mode'];
  enabled?: boolean;
  adapter_config?: Record<string, unknown>;
}

function schemaProperties(adapter: AdapterMetadata | undefined): Record<string, Record<string, unknown>> {
  const schema = adapter?.platform_config_schema as { properties?: Record<string, Record<string, unknown>> } | undefined;
  return schema?.properties ?? {};
}

export function ProjectPlatformForm(props: ProjectPlatformFormProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [adapters, setAdapters] = useState<AdapterMetadata[]>([]);
  const [adapterKey, setAdapterKey] = useState('generic-http');
  const [resolverType, setResolverType] = useState<'BASE_URL' | 'SERVICE_DISCOVERY'>('BASE_URL');
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    getAdapters()
      .then((page) => setAdapters(page.items))
      .catch(() => setAdapters([]));
  }, []);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    const platform = props.platform;
    const values: Record<string, unknown> = platform
      ? {
          name: platform.name,
          key: platform.key,
          resolver_type: platform.resolver_type,
          base_url: String(platform.resolver_config.base_url ?? ''),
          service_name: String(platform.resolver_config.service_name ?? ''),
          adapter_key: platform.adapter_key,
          credential_mode: platform.credential_mode,
          enabled: platform.enabled
        }
      : { resolver_type: 'BASE_URL', adapter_key: 'generic-http', credential_mode: 'NONE', enabled: true };
    for (const [key, value] of Object.entries(platform?.adapter_config ?? {})) {
      values[`adapter_config.${key}`] = value;
    }
    formApi.current?.setValues(values);
    setAdapterKey(platform?.adapter_key ?? 'generic-http');
    setResolverType(platform?.resolver_type ?? 'BASE_URL');
  }, [props.visible, props.platform]);

  const selectedAdapter = useMemo(
    () => adapters.find((adapter) => adapter.key === adapterKey),
    [adapters, adapterKey]
  );
  const adapterProperties = schemaProperties(selectedAdapter);

  const submit = async (values: FormValues): Promise<void> => {
    setSaving(true);
    const resolverConfig =
      values.resolver_type === 'BASE_URL'
        ? { base_url: values.base_url ?? '' }
        : { service_name: values.service_name ?? '' };
    const payload: PlatformSaveInput = {
      name: values.name,
      resolver_type: values.resolver_type,
      resolver_config: resolverConfig,
      adapter_key: values.adapter_key,
      adapter_config: values.adapter_config ?? {},
      credential_mode: values.credential_mode,
      enabled: values.enabled ?? true
    };
    try {
      if (props.platform) {
        const result = await updatePlatform(props.platform.platform_id, payload);
        props.onSaved(result.platform_id, result.credential_reconfigure_required);
      } else {
        payload.key = values.key;
        const result = await createPlatform(payload);
        props.onSaved(result.platform_id, false);
      }
    } catch {
      // ApiClient 展示本地化错误，表单保留供重试
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={560}
      title={props.platform ? t('platform.form.editTitle') : t('platform.form.createTitle')}
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
        label={t('platform.form.name')}
        rules={[{ required: true, message: t('platform.form.name') }]}
      />
      <Form.Input
        field="key"
        label={t('platform.form.key')}
        disabled={props.platform !== null}
        extraText={props.platform ? t('platform.form.keyImmutable') : undefined}
        rules={props.platform ? [] : [{ required: true, message: t('platform.form.key') }]}
      />
      <Form.Select
        field="resolver_type"
        label={t('platform.form.resolverType')}
        initValue="BASE_URL"
        optionList={[
          { value: 'BASE_URL', label: t('platform.resolverType.BASE_URL') },
          { value: 'SERVICE_DISCOVERY', label: t('platform.resolverType.SERVICE_DISCOVERY') }
        ]}
        onChange={(value) => setResolverType(String(value) as 'BASE_URL' | 'SERVICE_DISCOVERY')}
        rules={[{ required: true, message: t('platform.form.resolverType') }]}
      />
      {resolverType === 'BASE_URL' ? (
        <Form.Input
          field="base_url"
          label={t('platform.form.baseUrl')}
          extraText={t('platform.form.baseUrlHint')}
          rules={[{ required: true, message: t('platform.form.baseUrl') }]}
        />
      ) : (
        <Form.Input
          field="service_name"
          label={t('platform.form.serviceName')}
          rules={[{ required: true, message: t('platform.form.serviceName') }]}
        />
      )}
      <Form.Select
        field="adapter_key"
        label={t('platform.form.adapter')}
        optionList={adapters.map((adapter) => ({ value: adapter.key, label: `${adapter.name} · ${adapter.version}` }))}
        onChange={(value) => setAdapterKey(String(value))}
        rules={[{ required: true, message: t('platform.form.adapter') }]}
      />
      {Object.entries(adapterProperties).map(([property, definition]) => {
        const field = `adapter_config.${property}`;
        const label = String(definition.title ?? property);
        const enumValues = Array.isArray(definition.enum) ? (definition.enum as string[]) : null;
        if (enumValues) {
          return (
            <Form.Select
              key={field}
              field={field}
              label={label}
              optionList={enumValues.map((value) => ({ value, label: value }))}
            />
          );
        }
        if (definition.type === 'integer' || definition.type === 'number') {
          return <Form.InputNumber key={field} field={field} label={label} />;
        }
        return <Form.Input key={field} field={field} label={label} />;
      })}
      <Form.Select
        field="credential_mode"
        label={t('platform.form.credentialMode')}
        initValue="NONE"
        optionList={[
          { value: 'USER_ONLY', label: t('platform.credentialMode.USER_ONLY') },
          { value: 'SHARED_ONLY', label: t('platform.credentialMode.SHARED_ONLY') },
          { value: 'USER_THEN_SHARED', label: t('platform.credentialMode.USER_THEN_SHARED') },
          { value: 'NONE', label: t('platform.credentialMode.NONE') }
        ]}
        rules={[{ required: true, message: t('platform.form.credentialMode') }]}
      />
      <Form.Switch field="enabled" label={t('platform.form.enabled')} initValue />
    </FormModal>
  );
}
