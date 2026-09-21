import { Form } from '@douyinfe/semi-ui';

import type { AdapterMetadata } from './services/platforms';

export interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
}

export function credentialFields(adapter: AdapterMetadata | null): CredentialField[] {
  const schema = adapter?.credential_schema as
    | { properties?: Record<string, { title?: string; 'x-secret'?: boolean }> }
    | undefined;
  return Object.entries(schema?.properties ?? {}).map(([name, definition]) => ({
    name,
    label: definition.title ?? name,
    secret: definition['x-secret'] === true
  }));
}

export interface CredentialValidationError {
  field?: string;
  key: string;
}

export function validateCredentialValues(
  fields: CredentialField[],
  values: Record<string, string>
): CredentialValidationError | null {
  const filled = (name: string): boolean => (values[name] ?? '').trim().length > 0;
  if (fields.length > 0 && !fields.some((field) => filled(field.name))) {
    return { field: fields[0].name, key: 'platform.credentials.errorRequired' };
  }
  const hasUserField = fields.some((field) => field.name === 'username');
  const hasPasswordField = fields.some((field) => field.name === 'password');
  if (hasUserField && hasPasswordField && filled('username') !== filled('password')) {
    return {
      field: filled('username') ? 'password' : 'username',
      key: 'platform.credentials.errorPair'
    };
  }
  return null;
}

export interface CredentialSchemaFieldsProps {
  adapter: AdapterMetadata | null;
  values: Record<string, string>;
  onChange(name: string, value: string): void;
}

export function CredentialSchemaFields(props: CredentialSchemaFieldsProps) {
  return (
    <>
      {credentialFields(props.adapter).map((field) => (
        <Form.Input
          key={field.name}
          field={field.name}
          label={field.label}
          mode={field.secret ? 'password' : undefined}
          onChange={(value: string) => props.onChange(field.name, value)}
        />
      ))}
    </>
  );
}
