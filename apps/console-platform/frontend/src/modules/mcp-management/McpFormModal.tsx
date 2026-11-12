import { Banner, Button, Form, Tag } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody, newRequestId } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { FormModal } from '../../components/common/FormModal';
import { Tips } from '../../components/common/Tips';
import { buildAuthPayload, headersIssueKey, headersJsonForServer } from './authPayload';
import { McpConfigImport, type ImportedMcpConfig } from './McpConfigImport';
import {
  createMcpServer,
  discoverTools,
  updateMcpServer,
  type McpSaveInput,
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
  headers_json: string;
  connect_timeout_ms?: number;
  tool_cache_ttl_sec?: number;
}

const VALID_ENDPOINT = /^https?:\/\/.+/;
const DEFAULT_CONNECT_TIMEOUT_MS = 5000;
const DEFAULT_TOOL_CACHE_TTL_SEC = 300;

export function McpFormModal(props: McpFormModalProps) {
  const { t } = useTranslation();
  const { account } = useAuth();
  const isAdmin = account?.role === 'ADMIN';
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [timeoutMs, setTimeoutMs] = useState(DEFAULT_CONNECT_TIMEOUT_MS);
  const [cacheTtlSec, setCacheTtlSec] = useState(DEFAULT_TOOL_CACHE_TTL_SEC);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    const server = props.server;
    formApi.current?.reset();
    setFormError(null);
    setAdvancedOpen(false);
    setTimeoutMs(server?.connect_timeout_ms ?? DEFAULT_CONNECT_TIMEOUT_MS);
    setCacheTtlSec(server?.tool_cache_ttl_sec ?? DEFAULT_TOOL_CACHE_TTL_SEC);
    formApi.current?.setValues({
      name: server?.name,
      key: server?.key,
      endpoint: server?.endpoint,
      user_scope: server?.user_scope ?? 'SELECTED',
      enabled: server?.enabled === false ? 'false' : 'true',
      headers_json: headersJsonForServer(
        server?.auth_config ?? null,
        server?.auth_secret_configured ?? false
      ),
      connect_timeout_ms: server?.connect_timeout_ms ?? DEFAULT_CONNECT_TIMEOUT_MS,
      tool_cache_ttl_sec: server?.tool_cache_ttl_sec ?? DEFAULT_TOOL_CACHE_TTL_SEC
    });
  }, [props.visible, props.server]);

  const applyImport = (config: ImportedMcpConfig): void => {
    formApi.current?.setValues({
      name: config.name,
      endpoint: config.endpoint,
      headers_json: config.headersJson
    });
  };

  const submit = async (values: FormValues): Promise<void> => {
    if (!VALID_ENDPOINT.test(values.endpoint)) {
      formApi.current?.setError('endpoint', t('mcp.form.endpointInvalid'));
      Tips.error(t('mcp.form.endpointInvalid'));
      return;
    }
    const auth = buildAuthPayload(values.headers_json, {
      editing: props.server !== null,
      secretConfigured: props.server?.auth_secret_configured ?? false
    });
    if ('issue' in auth) {
      const message = t(headersIssueKey(auth.issue));
      formApi.current?.setError('headers_json', message);
      Tips.error(message);
      return;
    }
    setSaving(true);
    setFormError(null);
    const input: McpSaveInput = {
      name: values.name,
      endpoint: values.endpoint,
      enabled: values.enabled === 'true',
      auth_config: auth.auth_config,
      connect_timeout_ms: values.connect_timeout_ms,
      tool_cache_ttl_sec: values.tool_cache_ttl_sec
    };
    if (auth.auth_secret !== undefined) {
      input.auth_secret = auth.auth_secret;
    }
    try {
      if (props.server) {
        await updateMcpServer(props.server.mcp_id, input, { errorTip: 'never' });
      } else {
        const created = await createMcpServer(
          {
            ...input,
            key: values.key,
            user_scope: isAdmin ? values.user_scope : 'SELECTED'
          },
          newRequestId(),
          { errorTip: 'never' }
        );
        // Saving and discovering are separate outcomes: a discovery failure keeps the
        // server saved and surfaces the API error, so the modal still closes.
        await discoverTools(created.mcp_id).catch(() => undefined);
      }
      props.onSaved();
    } catch (error) {
      const body = apiErrorBody(error);
      if (body?.code === 'MCP_CONFIG_INVALID') {
        formApi.current?.setError('headers_json', body.msg);
      } else if (body?.msg) {
        setFormError(body.msg);
      } else {
        setFormError(t('common.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  const title = props.server ? t('mcp.form.editTitle') : t('mcp.form.createTitle');
  const advancedCustomized = timeoutMs !== DEFAULT_CONNECT_TIMEOUT_MS
    || cacheTtlSec !== DEFAULT_TOOL_CACHE_TTL_SEC;

  return (
    <FormModal
      visible={props.visible}
      width={720}
      title={(
        <span>
          {title} <Tag size="small" type="ghost">{t('mcp.form.transportStatic')}</Tag>
        </span>
      )}
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
          <McpConfigImport onImported={applyImport} />
        </div>
        <div className="form-field-full">
          <Form.TextArea
            field="headers_json"
            label={t('mcp.form.headersJson')}
            initValue="{}"
            autosize={{ minRows: 6, maxRows: 14 }}
            data-testid="mcp-headers-json"
            extraText={t('mcp.form.headersJsonHint')}
          />
        </div>
        {!props.server && isAdmin ? <Form.Select
          field="user_scope"
          label={t('mcp.columns.userScope')}
          initValue="SELECTED"
          optionList={[
            { value: 'SELECTED', label: t('mcp.scope.selected') },
            { value: 'ALL', label: t('mcp.scope.all') }
          ]}
        /> : null}
        <Form.Select
          field="enabled"
          label={t('mcp.columns.enabled')}
          initValue="true"
          optionList={[
            { value: 'true', label: t('common.status.enabled') },
            { value: 'false', label: t('common.status.disabled') }
          ]}
        />
        <div className="form-section">
          <Button
            theme="borderless"
            data-testid="mcp-advanced-toggle"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            {t('mcp.form.advancedToggle')}
          </Button>
          {advancedCustomized ? (
            <Tag size="small">{t('mcp.form.advancedCustomized')}</Tag>
          ) : null}
          <div className="form-section-hint">{t('mcp.form.advancedSectionHint')}</div>
        </div>
        <div
          className="form-field-full"
          style={{ display: advancedOpen ? undefined : 'none' }}
        >
          <div className="form-grid">
            <Form.InputNumber
              field="connect_timeout_ms"
              label={t('mcp.form.connectTimeout')}
              initValue={DEFAULT_CONNECT_TIMEOUT_MS}
              min={100}
              max={60000}
              onChange={(value) => setTimeoutMs(
                typeof value === 'number' ? value : DEFAULT_CONNECT_TIMEOUT_MS
              )}
            />
            <Form.InputNumber
              field="tool_cache_ttl_sec"
              label={t('mcp.form.cacheTtl')}
              initValue={DEFAULT_TOOL_CACHE_TTL_SEC}
              min={1}
              max={86400}
              onChange={(value) => setCacheTtlSec(
                typeof value === 'number' ? value : DEFAULT_TOOL_CACHE_TTL_SEC
              )}
            />
          </div>
        </div>
        <div className="form-field-banner">
          <Banner type="info" closeIcon={null} description={t('mcp.form.banner')} />
        </div>
      </div>
    </FormModal>
  );
}
