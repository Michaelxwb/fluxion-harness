import { Modal, Spin } from '@douyinfe/semi-ui';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DetailGrid } from '../../components/common/DetailGrid';
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

  useEffect(() => {
    if (!props.visible || !props.serverId) {
      setResult(null);
      return;
    }
    setLoading(true);
    testMcp(props.serverId)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false));
  }, [props.visible, props.serverId]);

  return (
    <Modal
      visible={props.visible}
      title={t('mcp.test.title')}
      footer={null}
      width={480}
      onCancel={props.onCancel}
    >
      {loading || result === null ? (
        <Spin />
      ) : (
        <DetailGrid
          items={[
            {
              label: t('mcp.columns.connectionStatus'),
              value: t(`mcp.connection.${result.connection_status === 'AVAILABLE' ? 'AVAILABLE' : 'UNAVAILABLE'}`)
            },
            { label: t('mcp.test.latency'), value: `${result.latency_ms} ms` },
            { label: t('mcp.test.serverInfo'), value: result.server_info?.name ?? '-' },
            ...(result.error_code ? [{ label: t('mcp.test.errorCode'), value: result.error_code }] : [])
          ]}
        />
      )}
    </Modal>
  );
}
