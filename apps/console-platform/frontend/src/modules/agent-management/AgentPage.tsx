import { Button, Input, Popconfirm, Select, Tag, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { deleteAgent, listAgents, type AgentDetail, type AgentListItem } from './services/agents';
import { AgentDetailSideSheet } from './AgentDetailSideSheet';
import { AgentFormModal } from './AgentFormModal';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', enabled: '' };

export function AgentPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [items, setItems] = useState<AgentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formAgent, setFormAgent] = useState<AgentDetail | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const page = await listAgents({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        enabled: params.enabled === '' ? undefined : params.enabled === 'true'
      });
      setItems(page.items);
      setTotal(page.total);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openDetail = async (agent: AgentListItem): Promise<void> => {
    setDetailId(agent.id);
    setDetail({ ...agent, instructions: '', runtime_config: {}, create_time: '' });
  };

  const copyId = async (agent: AgentListItem): Promise<void> => {
    await navigator.clipboard.writeText(agent.id);
    Toast.success(t('agent.actions.idCopied'));
  };

  const remove = async (agent: AgentListItem): Promise<void> => {
    await deleteAgent(agent.id);
    if (detailId === agent.id) {
      setDetail(null);
      setDetailId(null);
    }
    await reload();
  };

  const detailForSheet = detail ?? (items.find((a) => a.id === detailId) as AgentDetail | undefined) ?? null;

  return (
    <>
      <PageHeader title={t('agent.title')} description={t('agent.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button theme="solid" data-testid="create-agent" onClick={() => {
              setFormAgent(null);
              setFormVisible(true);
            }}>
              {t('agent.actions.create')}
            </Button>
          }
          search={
            <>
              <Input
                value={params.keyword}
                placeholder={t('agent.searchPlaceholder')}
                style={{ width: 220 }}
                onChange={(value) => setParams((prev) => ({ ...prev, keyword: value, page: 1 }))}
              />
              <Select
                value={params.enabled || undefined}
                style={{ width: 140 }}
                showClear
                placeholder={t('agent.columns.enabled')}
                optionList={[
                  { value: 'true', label: t('common.status.enabled') },
                  { value: 'false', label: t('common.status.disabled') }
                ]}
                onChange={(value) =>
                  setParams((prev) => ({ ...prev, enabled: value ? String(value) : '', page: 1 }))
                }
              />
              <Button onClick={() => void reload()}>{t('common.refresh')}</Button>
            </>
          }
        />
        <RemoteTable<AgentListItem>
          rowKey="id"
          loading={loading}
          columns={[
            {
              title: t('agent.form.name'),
              dataIndex: 'name',
              render: (value: string, record: AgentListItem) => (
                <Button theme="borderless" data-testid={`agent-link-${record.key}`} onClick={() => void openDetail(record)}>
                  {value}
                </Button>
              )
            },
            { title: t('agent.form.key'), dataIndex: 'key' },
            { title: t('agent.form.model'), dataIndex: 'model_name' },
            { title: t('agent.columns.skillCount'), dataIndex: 'skill_count' },
            { title: t('agent.columns.mcpCount'), dataIndex: 'mcp_count' },
            { title: t('agent.columns.channelCount'), dataIndex: 'channel_count' },
            { title: t('agent.columns.userCount'), dataIndex: 'user_count' },
            {
              title: t('agent.columns.enabled'),
              dataIndex: 'enabled',
              render: (value: boolean) => (
                <Tag color={value ? 'green' : 'grey'}>
                  {t(value ? 'common.status.enabled' : 'common.status.disabled')}
                </Tag>
              )
            },
            { title: 'revision', dataIndex: 'revision' },
            {
              title: t('agent.columns.updateTime'),
              dataIndex: 'update_time',
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              title: t('agent.columns.actions'),
              render: (_: unknown, record: AgentListItem) => (
                <>
                  <Button theme="borderless" onClick={() => void copyId(record)}>
                    {t('agent.actions.copyId')}
                  </Button>
                  <Popconfirm title={t('agent.confirmDelete')} onConfirm={() => void remove(record)}>
                    <Button theme="borderless" type="danger">
                      {t('agent.actions.delete')}
                    </Button>
                  </Popconfirm>
                </>
              )
            }
          ]}
          dataSource={items}
          page={params.page}
          pageSize={params.page_size}
          total={total}
          onPageChange={(page) => setParams((prev) => ({ ...prev, page }))}
          onPageSizeChange={(page_size) => setParams((prev) => ({ ...prev, page: 1, page_size }))}
          empty={<EmptyState title={t('common.empty')} description={t('common.emptyHint')} />}
        />
      </PageSection>
      <AgentFormModal
        visible={formVisible}
        agent={formAgent}
        onCancel={() => {
          setFormVisible(false);
          setFormAgent(null);
        }}
        onSaved={() => {
          setFormVisible(false);
          setFormAgent(null);
          setDetailReloadKey((k) => k + 1);
          void reload();
        }}
      />
      {detailForSheet ? (
        <AgentDetailSideSheet
          agentId={detailForSheet.id}
          reloadKey={detailReloadKey}
          onClose={() => setDetail(null)}
          onEdit={async () => {
            const { getAgent } = await import('./services/agents');
            const fresh = await getAgent(detailForSheet.id);
            setFormAgent(fresh);
            setFormVisible(true);
          }}
          onMutated={() => void reload()}
        />
      ) : null}
    </>
  );
}
