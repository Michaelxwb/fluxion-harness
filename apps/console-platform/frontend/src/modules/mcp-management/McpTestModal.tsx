import { Modal, Spin } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
import { ErrorState } from '../../components/common/ErrorState';
import { testMcp, type McpTestResult } from './services/mcpServers';

export interface McpTestModalProps {
  visible: boolean;
  serverId: string | null;
  onCancel(): void;
}

export function McpTestModal(props: McpTestModalProps) {
  const { t } = useTranslation();
  const [result, setResult] = useState<McpTestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!props.visible || !props.serverId) {
      setResult(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    setResult(null);
    setFailed(false);
    setLoading(true);
    testMcp(props.serverId)
      .then((value) => {
        if (!cancelled) {
          setResult(value);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [props.visible, props.serverId]);

  return (
    <Modal
      visible={props.visible}
      title={t('mcp.test.title')}
      footer={null}
      width={480}
      onCancel={props.onCancel}
    >
      {failed ? (
        <ErrorState />
      ) : loading || result === null ? (
        <Spin style={{ display: 'block', margin: '16px auto' }} />
      ) : (
        <DetailGrid
          items={[
            {
              label: t('mcp.columns.connectionStatus'),
              value: t(
                `mcp.connection.${result.connection_status === 'AVAILABLE' ? 'AVAILABLE' : 'UNAVAILABLE'}`
              )
            },
            { label: t('mcp.test.latency'), value: `${result.latency_ms} ms` },
            {
              label: t('mcp.test.serverInfo'),
              value: result.server_info
                ? `${result.server_info.name ?? '-'} · ${result.server_info.version ?? '-'}`
                : '-'
            },
            ...(result.error_code ? [{ label: t('mcp.test.errorCode'), value: result.error_code }] : [])
          ]}
        />
      )}
    </Modal>
  );
}
