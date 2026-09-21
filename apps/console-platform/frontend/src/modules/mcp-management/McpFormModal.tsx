import { Banner, Form, Toast } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody, newRequestId } from '../../api/client';
import { FormModal } from '../../components/common/FormModal';
import {
  createMcpServer,
  discoverTools,
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
  enabled: 'true' | 'false';
  auth_secret?: string;
  auth_config?: string;
  connect_timeout_ms?: number;
  tool_cache_ttl_sec?: number;
}

const VALID_ENDPOINT = /^https?:\/\/.+/;

export function McpFormModal(props: McpFormModalProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.reset();
    setFormError(null);
    formApi.current?.setValues({ user_scope: 'SELECTED', enabled: 'true' });
    if (props.server) {
      formApi.current?.setValues({
        name: props.server.name,
        key: props.server.key,
        endpoint: props.server.endpoint,
        user_scope: props.server.user_scope,
        enabled: props.server.enabled ? 'true' : 'false',
        auth_config: JSON.stringify(props.server.auth_config ?? {}, null, 2),
        connect_timeout_ms: props.server.connect_timeout_ms,
        tool_cache_ttl_sec: props.server.tool_cache_ttl_sec
      });
    }
  }, [props.visible, props.server]);

  const submit = async (values: FormValues): Promise<void> => {
    // [E-09] transport 固定为 Streamable HTTP（只读字段）；endpoint 协议本地拦截，不提交
    if (!VALID_ENDPOINT.test(values.endpoint)) {
      Toast.error(t('mcp.form.endpointInvalid'));
      return;
    }
    let authConfig: Record<string, unknown> = {};
    const rawAuthConfig = (values.auth_config ?? '').trim();
    if (rawAuthConfig) {
      try {
        const parsed: unknown = JSON.parse(rawAuthConfig);
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('not an object');
        }
        authConfig = parsed as Record<string, unknown>;
      } catch {
        formApi.current?.setError('auth_config', t('mcp.form.authConfigInvalid'));
        return;
      }
    }
    setSaving(true);
    setFormError(null);
    const base = {
      name: values.name,
      endpoint: values.endpoint,
      user_scope: values.user_scope,
      enabled: values.enabled === 'true',
      auth_config: authConfig,
      connect_timeout_ms: values.connect_timeout_ms,
      tool_cache_ttl_sec: values.tool_cache_ttl_sec
    };
    try {
      if (props.server) {
        const input = { ...base };
        if (values.auth_secret) {
          (input as { auth_secret?: string }).auth_secret = values.auth_secret;
        }
        await updateMcpServer(props.server.mcp_id, input);
      } else {
        const created = await createMcpServer(
          { ...base, key: values.key, auth_secret: values.auth_secret || undefined },
          newRequestId()
        );
        // 交互稿「保存并发现工具」：注册成功后立即执行一次目录发现；
        // 失败不阻塞（服务已保存，可在详情中重试，Toast 由 ApiClient 展示）
        await discoverTools(created.mcp_id).catch(() => undefined);
      }
      props.onSaved();
    } catch (error) {
      const body = apiErrorBody(error);
      if (body?.code === 'MCP_CONFIG_INVALID') {
        formApi.current?.setError('endpoint', body.msg);
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
      title={props.server ? t('mcp.form.editTitle') : t('mcp.form.createTitle')}
      okText={props.server ? t('common.save') : t('mcp.form.saveAndDiscover')}
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
      {formError ? <Banner type="danger" closeIcon={null} description={formError} /> : null}
      <div className="form-grid">
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
        <div className="form-field-full">
          <Form.Input
            field="endpoint"
            label={t('mcp.form.endpoint')}
            rules={[{ required: true, message: t('mcp.form.endpoint') }]}
          />
        </div>
        <div className="form-field-full">
          <Form.Input
            field="transport"
            label={t('mcp.form.transport')}
            disabled
            initValue="streamable-http"
            extraText={t('mcp.form.transportHint')}
          />
        </div>
        <Form.Select
          field="user_scope"
          label={t('mcp.columns.userScope')}
          initValue="SELECTED"
          optionList={[
            { value: 'SELECTED', label: t('mcp.scope.selected') },
            { value: 'ALL', label: t('mcp.scope.all') }
          ]}
        />
        <Form.Select
          field="enabled"
          label={t('mcp.columns.enabled')}
          initValue="true"
          optionList={[
            { value: 'true', label: t('common.status.enabled') },
            { value: 'false', label: t('common.status.disabled') }
          ]}
        />
        <div className="form-section-title">{t('mcp.form.advancedSection')}</div>
        <div className="form-section-hint">{t('mcp.form.advancedSectionHint')}</div>
        <div className="form-field-full">
          <Form.Input
            field="auth_secret"
            label={t('mcp.form.authSecret')}
            mode="password"
            extraText={
              props.server?.auth_secret_configured
                ? t('mcp.form.authSecretKeep')
                : t('mcp.form.authSecretHint')
            }
          />
        </div>
        <div className="form-field-full">
          <Form.InputNumber
            field="connect_timeout_ms"
            label={t('mcp.form.connectTimeout')}
            initValue={5000}
            min={100}
            max={60000}
          />
        </div>
        <div className="form-field-full">
          <Form.InputNumber
            field="tool_cache_ttl_sec"
            label={t('mcp.form.cacheTtl')}
            initValue={300}
            min={1}
            max={86400}
          />
        </div>
        <div className="form-field-full">
          <Form.Input
            field="auth_config"
            label={t('mcp.form.authConfig')}
            placeholder='{"header": "X-Api-Key"}'
            extraText={t('mcp.form.authConfigHint')}
          />
        </div>
        <div className="form-field-banner">
          <Banner type="info" closeIcon={null} description={t('mcp.form.banner')} />
        </div>
      </div>
    </FormModal>
  );
}
