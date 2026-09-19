import { Form, Toast } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FormModal } from '../../components/common/FormModal';
import {
  createMcpServer,
  updateMcpServer,
  type McpServerDetail
} from './services/mcpServers';

export interface McpFormModalProps {
  visible: boolean;
  server: McpServerDetail | null;
  onCancel(): void;
  onSaved(): void;
}

interface FormValues {
  name: string;
  key?: string;
  endpoint: string;
  user_scope: 'ALL' | 'SELECTED';
  enabled?: boolean;
  auth_secret?: string;
  connect_timeout_ms?: number;
  tool_cache_ttl_sec?: number;
}

const VALID_ENDPOINT = /^https?:\/\/.+/;

export function McpFormModal(props: McpFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    formApi.current?.setValues({ user_scope: 'SELECTED' });
    if (props.server) {
      formApi.current?.setValues({
        name: props.server.name,
        endpoint: props.server.endpoint,
        user_scope: props.server.user_scope,
        enabled: props.server.enabled,
        connect_timeout_ms: props.server.connect_timeout_ms,
        tool_cache_ttl_sec: props.server.tool_cache_ttl_sec
      });
    }
  }, [props.visible, props.server]);

  const submit = async (values: FormValues): Promise<void> => {
    // [E-09] transport 固定；endpoint 协议本地拦截，不提交
    if (!VALID_ENDPOINT.test(values.endpoint)) {
      Toast.error(t('mcp.form.endpointInvalid'));
      return;
    }
    setSaving(true);
    const payload = {
      name: values.name,
      endpoint: values.endpoint,
      user_scope: values.user_scope,
      enabled: values.enabled ?? true,
      connect_timeout_ms: values.connect_timeout_ms,
      tool_cache_ttl_sec: values.tool_cache_ttl_sec
    };
    try {
      if (props.server) {
        const input = { ...payload };
        if (values.auth_secret) {
          (input as { auth_secret?: string }).auth_secret = values.auth_secret;
        }
        await updateMcpServer(props.server.mcp_id, input);
      } else {
        await createMcpServer(
          { ...payload, key: values.key, auth_secret: values.auth_secret || undefined },
          crypto.randomUUID()
        );
      }
      props.onSaved();
    } catch {
      // [E-08] MCP_CONFIG_INVALID 等错误由 ApiClient Toast；Modal 保留，本地表单不被覆盖
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormModal
      visible={props.visible}
      width={560}
      title={props.server ? t('mcp.form.editTitle') : t('mcp.form.createTitle')}
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
        label={t('mcp.form.name')}
        rules={[{ required: true, message: t('mcp.form.name') }]}
      />
      <Form.Input
        field="key"
        label={t('mcp.form.key')}
        disabled={props.server !== null}
        rules={props.server ? [] : [{ required: true, message: t('mcp.form.key') }]}
      />
      <Form.Select
        field="transport"
        label={t('mcp.form.transport')}
        disabled
        initValue="streamable-http"
        extraText={t('mcp.form.transportHint')}
        optionList={[{ value: 'streamable-http', label: 'Streamable HTTP' }]}
      />
      <Form.Input
        field="endpoint"
        label={t('mcp.form.endpoint')}
        placeholder="https://mcp.example.com/mcp"
        rules={[{ required: true, message: t('mcp.form.endpoint') }]}
      />
      <Form.Input
        field="auth_secret"
        label={t('mcp.form.authSecret')}
        mode="password"
        placeholder={props.server?.auth_secret_configured ? t('mcp.form.authSecretKeep') : t('mcp.form.authSecretHint')}
      />
      <Form.Select
        field="user_scope"
        label={t('mcp.columns.userScope')}
        optionList={[
          { value: 'SELECTED', label: t('mcp.scope.selected') },
          { value: 'ALL', label: t('mcp.scope.all') }
        ]}
      />
      <Form.Switch field="enabled" label={t('mcp.columns.enabled')} initValue />
      <Form.InputNumber field="connect_timeout_ms" label={t('mcp.form.connectTimeout')} min={100} max={60000} />
      <Form.InputNumber field="tool_cache_ttl_sec" label={t('mcp.form.cacheTtl')} min={1} max={86400} />
    </FormModal>
  );
}
