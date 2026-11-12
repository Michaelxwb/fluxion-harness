import { Banner, Button, Form, Tag } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { apiErrorBody, newRequestId } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { FormModal } from '../../components/common/FormModal';
import { Tips } from '../../components/common/Tips';
import {
  buildAuthPayload,
  headerIssueKey,
  summarizeAuthMode,
  validateHeaderRows,
  type AuthMode,
  type HeaderRow
} from './authPayload';
import { AuthModeFields } from './AuthModeFields';
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
  auth_mode: AuthMode;
  auth_secret?: string;
  secret_header?: string;
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
  const [authMode, setAuthMode] = useState<AuthMode>('none');
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [secretHeader, setSecretHeader] = useState('');
  const [secretValue, setSecretValue] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [timeoutMs, setTimeoutMs] = useState(DEFAULT_CONNECT_TIMEOUT_MS);
  const [cacheTtlSec, setCacheTtlSec] = useState(DEFAULT_TOOL_CACHE_TTL_SEC);
  const formApi = useRef<FormApi | null>(null);

  useEffect(() => {
    if (!props.visible) {
      return;
    }
    const server = props.server;
    const config = server?.auth_config ?? null;
    const summary = summarizeAuthMode(config, server?.auth_secret_configured ?? false);
    const rows = Object.entries(config?.headers ?? {}).map(([name, value]) => ({ name, value }));
    formApi.current?.reset();
    setFormError(null);
    setAuthMode(summary.mode);
    setHeaders(rows);
    setSecretHeader(config?.secret_header ?? '');
    setSecretValue('');
    setAdvancedOpen(false);
    setTimeoutMs(server?.connect_timeout_ms ?? DEFAULT_CONNECT_TIMEOUT_MS);
    setCacheTtlSec(server?.tool_cache_ttl_sec ?? DEFAULT_TOOL_CACHE_TTL_SEC);
    formApi.current?.setValues({
      name: server?.name,
      key: server?.key,
      endpoint: server?.endpoint,
      user_scope: server?.user_scope ?? 'SELECTED',
      enabled: server?.enabled === false ? 'false' : 'true',
      auth_mode: summary.mode,
      auth_secret: '',
      secret_header: config?.secret_header ?? '',
      connect_timeout_ms: server?.connect_timeout_ms ?? DEFAULT_CONNECT_TIMEOUT_MS,
      tool_cache_ttl_sec: server?.tool_cache_ttl_sec ?? DEFAULT_TOOL_CACHE_TTL_SEC
    });
  }, [props.visible, props.server]);

  // Conditional fields mount/unmount with the auth mode, so mirror the local state into
  // the form after the field exists (e.g. an imported bearer token when starting from none).
  useEffect(() => {
    if (!props.visible) {
      return;
    }
    formApi.current?.setValue('auth_secret', secretValue);
    formApi.current?.setValue('secret_header', secretHeader);
  }, [props.visible, authMode, secretValue, secretHeader]);

  const changeAuthMode = (mode: AuthMode): void => {
    setAuthMode(mode);
    setSecretValue('');
    formApi.current?.setValue('auth_secret', '');
    if (mode !== 'headers') {
      setHeaders([]);
      setSecretHeader('');
      formApi.current?.setValue('secret_header', '');
    }
  };

  const applyImport = (config: ImportedMcpConfig): void => {
    setAuthMode(config.authMode);
    setHeaders(config.headers);
    setSecretHeader(config.secretHeader);
    setSecretValue(config.authSecret);
    formApi.current?.setValues({
      name: config.name,
      endpoint: config.endpoint,
      auth_mode: config.authMode,
      secret_header: config.secretHeader,
      auth_secret: config.authSecret
    });
  };

  const submit = async (values: FormValues): Promise<void> => {
    if (!VALID_ENDPOINT.test(values.endpoint)) {
      formApi.current?.setError('endpoint', t('mcp.form.endpointInvalid'));
      Tips.error(t('mcp.form.endpointInvalid'));
      return;
    }
    const secretConfigured = props.server?.auth_secret_configured ?? false;
    if (authMode === 'headers') {
      const issues = validateHeaderRows(headers, secretHeader, secretValue, secretConfigured);
      if (issues.length > 0) {
        Tips.error(t(headerIssueKey(issues[0].kind)));
        return;
      }
    }
    const auth = buildAuthPayload({
      auth_mode: authMode,
      auth_secret: secretValue,
      secret_header: secretHeader,
      headers
    });
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

  const title = props.server ? t('mcp.form.editTitle') : t('mcp.form.createTitle');
  const advancedCustomized = timeoutMs !== DEFAULT_CONNECT_TIMEOUT_MS
    || cacheTtlSec !== DEFAULT_TOOL_CACHE_TTL_SEC;
  const headerIssues = authMode === 'headers'
    ? validateHeaderRows(
      headers,
      secretHeader,
      secretValue,
      props.server?.auth_secret_configured ?? false
    )
    : [];

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
          <AuthModeFields
            mode={authMode}
            editing={props.server !== null}
            secretConfigured={props.server?.auth_secret_configured ?? false}
            headers={headers}
            secretHeader={secretHeader}
            secretValue={secretValue}
            issues={headerIssues}
            onModeChange={changeAuthMode}
            onHeadersChange={setHeaders}
            onSecretHeaderChange={setSecretHeader}
            onSecretValueChange={setSecretValue}
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
