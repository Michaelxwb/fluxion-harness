import { Button, Modal, TextArea } from '@douyinfe/semi-ui';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Tips } from '../../components/common/Tips';

export interface ImportedMcpConfig {
  name: string;
  endpoint: string;
  headersJson: string;
}

export interface McpConfigImportOutcome {
  config: ImportedMcpConfig | null;
  skippedStdio: number;
  invalid: boolean;
}

export interface McpConfigImportProps {
  onImported(config: ImportedMcpConfig): void;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function isStdioEntry(entry: Record<string, unknown>): boolean {
  const type = typeof entry.type === 'string' ? entry.type.toLowerCase() : '';
  const transport = typeof entry.transport === 'string' ? entry.transport.toLowerCase() : '';
  return 'command' in entry || type === 'stdio' || transport === 'stdio';
}

function stringHeaders(value: unknown): Record<string, string> {
  const record = asRecord(value);
  if (record === null) return {};
  const headers: Record<string, string> = {};
  for (const [name, headerValue] of Object.entries(record)) {
    if (typeof headerValue === 'string') headers[name] = headerValue;
  }
  return headers;
}

function importEntry(name: string, entry: Record<string, unknown>): ImportedMcpConfig | null {
  const url = entry.url;
  if (typeof url !== 'string' || url.trim() === '') return null;
  const headers = stringHeaders(entry.headers);
  const displayName = typeof entry.name === 'string' && entry.name.trim() !== ''
    ? entry.name
    : name;
  return {
    name: displayName,
    endpoint: url.trim(),
    headersJson: JSON.stringify(headers, null, 2)
  };
}

/**
 * Parses a Claude Desktop style mcpServers payload and fills the single headers
 * JSON input. The form then splits Bearer/sensitive headers before submit.
 */
export function parseMcpServersConfig(raw: string): McpConfigImportOutcome {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { config: null, skippedStdio: 0, invalid: true };
  }
  const root = asRecord(parsed);
  if (root === null) return { config: null, skippedStdio: 0, invalid: true };
  const servers = asRecord(root.mcpServers) ?? root;
  let skippedStdio = 0;
  for (const [name, value] of Object.entries(servers)) {
    const entry = asRecord(value);
    if (entry === null) continue;
    if (isStdioEntry(entry)) {
      skippedStdio += 1;
      continue;
    }
    const config = importEntry(name, entry);
    if (config !== null) return { config, skippedStdio, invalid: false };
  }
  return { config: null, skippedStdio, invalid: false };
}

export function McpConfigImport(props: McpConfigImportProps) {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [raw, setRaw] = useState('');

  const close = (): void => {
    setVisible(false);
    setRaw('');
  };

  const apply = (): void => {
    const outcome = parseMcpServersConfig(raw);
    if (outcome.config !== null) {
      props.onImported(outcome.config);
      if (outcome.skippedStdio > 0) {
        Tips.warning(t('mcp.form.importStdioUnsupported'));
      }
      close();
      return;
    }
    if (outcome.skippedStdio > 0) {
      Tips.warning(t('mcp.form.importStdioUnsupported'));
      return;
    }
    Tips.error(t('mcp.form.importInvalid'));
  };

  return (
    <>
      <Button
        theme="borderless"
        data-testid="mcp-import-config"
        onClick={() => setVisible(true)}
      >
        {t('mcp.form.importConfig')}
      </Button>
      <Modal
        visible={visible}
        width={560}
        zIndex={1100}
        title={t('mcp.form.importConfig')}
        okText={t('mcp.form.importConfig')}
        cancelText={t('common.cancel')}
        onOk={apply}
        onCancel={close}
      >
        <div className="form-section-hint">{t('mcp.form.importHint')}</div>
        <TextArea
          value={raw}
          autosize={{ minRows: 8, maxRows: 16 }}
          placeholder='{"mcpServers": {"demo": {"url": "https://example.com/mcp"}}}'
          onChange={(value) => setRaw(value)}
        />
      </Modal>
    </>
  );
}
