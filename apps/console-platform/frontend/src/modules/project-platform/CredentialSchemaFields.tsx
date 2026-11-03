import { Form } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

export function parseCredentialJson(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    throw new Error('Credential must be a JSON object');
  }
  return value as Record<string, unknown>;
}

export interface CredentialSchemaFieldsProps {
  value: string;
  onChange(value: string): void;
}

export function CredentialSchemaFields(props: CredentialSchemaFieldsProps) {
  const { t } = useTranslation();
  return (
    <Form.TextArea
      field="credential_json"
      initValue={props.value}
      label={t('platform.credentials.jsonLabel')}
      placeholder={t('platform.credentials.jsonPlaceholder')}
      autosize={{ minRows: 8, maxRows: 16 }}
      autoComplete="off"
      onChange={props.onChange}
    />
  );
}
