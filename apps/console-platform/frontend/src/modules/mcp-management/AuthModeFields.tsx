import { Banner, Form, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import {
  headerIssueKey,
  type AuthMode,
  type HeaderRow,
  type HeaderValidationIssue
} from './authPayload';
import { HeaderRowsEditor } from './HeaderRowsEditor';

export interface AuthModeFieldsProps {
  mode: AuthMode;
  editing: boolean;
  secretConfigured: boolean;
  headers: HeaderRow[];
  secretHeader: string;
  secretValue: string;
  issues: HeaderValidationIssue[];
  onModeChange(mode: AuthMode): void;
  onHeadersChange(rows: HeaderRow[]): void;
  onSecretHeaderChange(value: string): void;
  onSecretValueChange(value: string): void;
}

function issueMessages(
  issues: HeaderValidationIssue[],
  predicate: (issue: HeaderValidationIssue) => boolean
): string[] {
  const kinds = issues.filter(predicate).map((issue) => headerIssueKey(issue.kind));
  return Array.from(new Set(kinds));
}

export function AuthModeFields(props: AuthModeFieldsProps) {
  const { t } = useTranslation();
  const secretIssues = props.issues.filter((issue) => issue.rowIndex === -1);
  const headerIssues = issueMessages(secretIssues, (issue) => issue.kind !== 'secretValueRequired');
  const valueIssues = issueMessages(secretIssues, (issue) => issue.kind === 'secretValueRequired');
  const secretExtraText = props.secretConfigured
    ? t('mcp.form.authSecretKeep')
    : t('mcp.form.authSecretHint');

  return (
    <>
      <Form.Select
        field="auth_mode"
        label={t('mcp.form.authMode')}
        data-testid="mcp-auth-mode"
        optionList={[
          { value: 'none', label: t('mcp.form.authModeNone') },
          { value: 'bearer', label: t('mcp.form.authModeBearer') },
          { value: 'headers', label: t('mcp.form.authModeHeaders') }
        ]}
        onChange={(value) => props.onModeChange(value as AuthMode)}
      />
      {props.mode === 'none' && props.editing && props.secretConfigured ? (
        <Banner
          type="warning"
          closeIcon={null}
          description={t('mcp.form.authNoneClearsSecret')}
        />
      ) : null}
      {props.mode === 'bearer' ? (
        <Form.Input
          field="auth_secret"
          label={t('mcp.form.bearerToken')}
          mode="password"
          autoComplete="new-password"
          data-testid="mcp-bearer-token"
          extraText={secretExtraText}
          onChange={(value) => props.onSecretValueChange(value)}
        />
      ) : null}
      {props.mode === 'headers' ? (
        <>
          <HeaderRowsEditor
            rows={props.headers}
            issues={props.issues}
            onChange={props.onHeadersChange}
          />
          <div className="form-grid">
            <div>
              <Form.Input
                field="secret_header"
                label={t('mcp.form.secretHeader')}
                data-testid="mcp-secret-header"
                extraText={t('mcp.form.secretHeaderHint')}
                onChange={(value) => props.onSecretHeaderChange(value)}
              />
              {headerIssues.map((key) => (
                <Typography.Text key={key} type="danger" size="small" style={{ display: 'block' }}>
                  {t(key)}
                </Typography.Text>
              ))}
            </div>
            <div>
              <Form.Input
                field="auth_secret"
                label={t('mcp.form.secretValue')}
                mode="password"
                autoComplete="new-password"
                data-testid="mcp-secret-value"
                extraText={secretExtraText}
                onChange={(value) => props.onSecretValueChange(value)}
              />
              {valueIssues.map((key) => (
                <Typography.Text key={key} type="danger" size="small" style={{ display: 'block' }}>
                  {t(key)}
                </Typography.Text>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </>
  );
}
