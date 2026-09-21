import { Button, Input, Select, Tag, Toast } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ConfirmAction } from '../../components/common/ConfirmAction';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { deleteAgent, getAgent, listAgents, type AgentDetail, type AgentListItem } from './services/agents';
import { AgentDetailSideSheet } from './AgentDetailSideSheet';
import { AgentFormModal } from './AgentFormModal';

const DEFAULT_PARAMS = { page: 1, page_size: 10, keyword: '', enabled: '' };

export function AgentPage() {
  const { t } = useTranslation();
  const [params, setParams] = useState(DEFAULT_PARAMS);
  const [keywordInput, setKeywordInput] = useState('');
  const [items, setItems] = useState<AgentListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [formAgent, setFormAgent] = useState<AgentDetail | null>(null);
  const [detailReloadKey, setDetailReloadKey] = useState(0);
  const requestSeq = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setParams((prev) =>
        prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
      );
    }, 300);
    return () => clearTimeout(timer);
  }, [keywordInput]);

  const reload = useCallback(async () => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const page = await listAgents({
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        enabled: params.enabled === '' ? undefined : params.enabled === 'true'
      });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [params]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const copyId = async (agent: AgentListItem): Promise<void> => {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('clipboard unavailable');
      }
      await navigator.clipboard.writeText(agent.id);
      Toast.success(t('agent.actions.idCopied'));
    } catch {
      Toast.error(t('agent.actions.copyFailed'));
    }
  };

  const remove = async (agent: AgentListItem): Promise<void> => {
    try {
      await deleteAgent(agent.id);
    } catch {
      return;
    }
    if (detailId === agent.id) {
      setDetailId(null);
    }
    if (items.length === 1 && params.page > 1) {
      setParams((prev) => ({ ...prev, page: prev.page - 1 }));
    } else {
      await reload();
    }
  };

  const openEdit = async (agentId: string): Promise<void> => {
    try {
      setFormAgent(await getAgent(agentId));
      setFormVisible(true);
    } catch {
      // 错误由 ApiClient 展示
    }
  };

  return (
    <>
      <PageHeader title={t('agent.title')} description={t('agent.subtitle')} />
      <PageSection>
        <ModuleToolbar
          actions={
            <Button
              theme="solid"
              data-testid="create-agent"
              onClick={() => {
                setFormAgent(null);
                setFormVisible(true);
              }}
            >
              {t('agent.actions.create')}
            </Button>
          }
          search={
            <>
              <Input
                value={keywordInput}
                placeholder={t('agent.searchPlaceholder')}
                style={{ width: 220 }}
                onChange={setKeywordInput}
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
                <Button
                  theme="borderless"
                  data-testid={`agent-link-${record.key}`}
                  onClick={() => setDetailId(record.id)}
                >
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
            { title: t('agent.detail.revision'), dataIndex: 'revision' },
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
                  <ConfirmAction danger title={t('agent.confirmDelete')} onConfirm={() => void remove(record)}>
                    {t('agent.actions.delete')}
                  </ConfirmAction>
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
          empty={
            failed ? (
              <ErrorState onRetry={() => void reload()} />
            ) : (
              <EmptyState title={t('common.empty')} description={t('common.emptyHint')} />
            )
          }
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
      {detailId ? (
        <AgentDetailSideSheet
          key={detailId}
          agentId={detailId}
          reloadKey={detailReloadKey}
          onClose={() => setDetailId(null)}
          onEdit={() => void openEdit(detailId)}
          onDelete={async () => {
            try {
              await deleteAgent(detailId);
            } catch {
              return;
            }
            setDetailId(null);
            await reload();
          }}
          onMutated={() => void reload()}
        />
      ) : null}
    </>
  );
}
