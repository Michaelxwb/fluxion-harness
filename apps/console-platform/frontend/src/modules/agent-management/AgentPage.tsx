import { actionColumn } from '../../components/common/tableLayout';
import { Button, Input, Select, Tag } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ListActionButton } from '../../components/common/ListActionButton';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { CopyableText } from '../../components/common/CopyableText';
import { PageHeader, PageSection } from '../../components/common/ConsolePage';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { EntityLink } from '../../components/common/EntityLink';
import { TableText } from '../../components/common/TableText';
import { ErrorState } from '../../components/common/ErrorState';
import { ModuleToolbar } from '../../components/common/ModuleToolbar';
import { RemoteTable } from '../../components/common/RemoteTable';
import { deleteAgent, getAgent, listAgents, type AgentDetail, type AgentListItem } from './services/agents';
import { AgentDetailSideSheet } from './AgentDetailSideSheet';
import { AgentFormModal } from './AgentFormModal';

const DEFAULT_PARAMS = { page: 1, page_size: 15, keyword: '', enabled: '' };

/** Text fields share 52% of extra space and summary fields share 48%. */
const COLUMN_GROWTH = {
  name: 0.18, key: 0.16, model_name: 0.18,
  skill_count: 0.08, mcp_count: 0.08, channel_count: 0.08,
  user_count: 0.08, enabled: 0.08, revision: 0.08
};

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

  /** 关键字：回车才写入查询参数并复位第 1 页（值未变则不产生新 params，避免无谓重查）。 */
  const commitKeyword = (): void => {
    setParams((prev) =>
      prev.keyword === keywordInput ? prev : { ...prev, keyword: keywordInput, page: 1 }
    );
  };

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
                onEnterPress={commitKeyword}
                showClear
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
              <ListActionButton onClick={() => void reload()} action="refresh" />
            </>
          }
        />
        <RemoteTable<AgentListItem>
          className="agent-table app-table-fit app-table-nowrap"
          scroll={{ x: '100%' }}
          columnGrowth={COLUMN_GROWTH}
          rowKey="id"
          loading={loading}
          columns={[
            {
              title: t('agent.form.name'),
              dataIndex: 'name',
              width: 48,
              render: (value: string, record: AgentListItem) => (
                <EntityLink
                  testId={`agent-link-${record.key}`}
                  onClick={() => setDetailId(record.id)}
                >
                  <TableText>{value}</TableText>
                </EntityLink>
              )
            },
            {
              title: t('agent.form.key'), dataIndex: 'key', width: 40,
              render: (value: string) => <TableText>{value}</TableText>
            },
            {
              title: t('agent.columns.id'), dataIndex: 'id', width: 120,
              render: (value: string) => <CopyableText display={value.slice(0, 8)} full={value} copyLabel={t('agent.actions.copyId')} />
            },
            {
              title: t('agent.form.model'), dataIndex: 'model_name', width: 52,
              render: (value: string) => <TableText>{value}</TableText>
            },
            { title: t('agent.columns.skillCount'), dataIndex: 'skill_count', width: 60 },
            { title: t('agent.columns.mcpCount'), dataIndex: 'mcp_count', width: 60 },
            { title: t('agent.columns.channelCount'), dataIndex: 'channel_count', width: 72 },
            { title: t('agent.columns.userCount'), dataIndex: 'user_count', width: 82 },
            {
              title: t('agent.columns.enabled'),
              dataIndex: 'enabled',
              width: 86,
              render: (value: boolean) => (
                <Tag color={value ? 'green' : 'grey'}>
                  {t(value ? 'common.status.enabled' : 'common.status.disabled')}
                </Tag>
              )
            },
            { title: t('agent.columns.revision'), dataIndex: 'revision', width: 72 },
            {
              title: t('agent.columns.updateTime'),
              dataIndex: 'update_time',
              width: 176,
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              ...actionColumn(112),
              title: t('agent.columns.actions'),
              render: (_: unknown, record: AgentListItem) => (
                <>
                  <Button theme="borderless" data-testid={`edit-agent-${record.key}`} onClick={() => void openEdit(record.id)}>
                    {t('agent.actions.edit')}
                  </Button>
                  <ConfirmAction title={t('agent.confirmDelete')} onConfirm={() => void remove(record)}>
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
          onMutated={() => void reload()}
        />
      ) : null}
    </>
  );
}
