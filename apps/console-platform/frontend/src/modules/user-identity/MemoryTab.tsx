/**
 * User-memory v2 governance tab: list, create, edit, enable/disable, delete,
 * restore from the recycle bin and read version history.
 */

import { Button, Form, SideSheet, Spin, Switch } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import { IconPlus } from '@douyinfe/semi-icons';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { FormModal } from '../../components/common/FormModal';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { TableText } from '../../components/common/TableText';
import type { MemoryConflictItem, MemoryHistoryItem } from './services/users';
import {
  commandMemory,
  listMemory,
  listMemoryConflicts,
  listMemoryHistory
} from './services/users';
import type { MemoryCommandInput, MemoryItem } from './memoryTypes';

const DEFAULT_PAGE_SIZE = 15;

export interface MemoryTabProps {
  userId: string;
  canManage: boolean;
  onMutated?(): void;
}

interface CreateValues {
  title: string;
  predicate: string;
  content: string;
  tags?: string;
}

interface EditValues {
  title: string;
  summary: string;
  content: string;
}

/** 可读化记忆内容：优先取主值，其余字段按 `key=value` 展平成一行。 */
export function formatMemoryContent(content: Record<string, unknown>): string {
  const value = content.value ?? content.text;
  if (typeof value === 'string') return value;
  return Object.entries(content)
    .map(([key, item]) => `${key}=${typeof item === 'string' ? item : JSON.stringify(item)}`)
    .join('；');
}

const KNOWN_AUTHORS = new Set(['USER', 'ADMIN', 'AGENT']);

function authorLabel(t: (key: string) => string, value: string): string {
  return KNOWN_AUTHORS.has(value) ? t(`user.memory.author.${value}`) : value;
}

export function MemoryTab(props: MemoryTabProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [recycle, setRecycle] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [createVisible, setCreateVisible] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [historyOf, setHistoryOf] = useState<MemoryItem | null>(null);
  const [history, setHistory] = useState<MemoryHistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [conflictsVisible, setConflictsVisible] = useState(false);
  const [conflicts, setConflicts] = useState<MemoryConflictItem[]>([]);
  const [conflictTotal, setConflictTotal] = useState(0);
  const [conflictLoading, setConflictLoading] = useState(false);
  const pendingCommandKeys = useRef<Record<string, string>>({});
  const formApi = useRef<FormApi<CreateValues> | null>(null);
  const editFormApi = useRef<FormApi<EditValues> | null>(null);

  /** 同一动作失败后的重试沿用同一命令号：Runtime 按回执重放，不会产生第二次效果。 */
  const runCommand = async (input: MemoryCommandInput) => {
    setActionError(null);
    const key = pendingCommandKeys.current[input.action] ?? newRequestId();
    pendingCommandKeys.current[input.action] = key;
    try {
      await commandMemory(props.userId, input, key);
      delete pendingCommandKeys.current[input.action];
      await load();
      props.onMutated?.();
    } catch {
      setActionError(t('user.memory.errorFallback'));
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const data = await listMemory(props.userId, {
        page,
        page_size: pageSize,
        include_deleted: recycle
      });
      setItems(data.items);
      setTotal(data.total);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, props.userId, recycle]);

  useEffect(() => {
    void load();
  }, [load]);



  const submitCreate = async (values: CreateValues) => {
    setCreating(true);
    setActionError(null);
    try {
      const tags = (values.tags ?? '')
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean);
      await commandMemory(
        props.userId,
        {
          action: 'CREATE',
          candidate: {
            predicate: values.predicate || 'general',
            scope_state: 'ABSENT',
            content: { value: values.content },
            title: values.title,
            summary: '',
            tags
          }
        },
        newRequestId()
      );
      setCreateVisible(false);
      formApi.current?.reset();
      await load();
      props.onMutated?.();
    } catch {
      setActionError(t('user.memory.errorFallback'));
    } finally {
      setCreating(false);
    }
  };

  const submitEdit = async (values: EditValues) => {
    if (editing === null) return;
    setSaving(true);
    setActionError(null);
    try {
      await commandMemory(
        props.userId,
        {
          action: 'UPDATE',
          memory_id: editing.id,
          expected_version: editing.version,
          updates: {
            title: values.title,
            summary: values.summary,
            content: { ...editing.content, text: values.content }
          }
        },
        newRequestId()
      );
      setEditing(null);
      await load();
      props.onMutated?.();
    } catch {
      setActionError(t('user.memory.error.MEMORY_VERSION_CONFLICT'));
    } finally {
      setSaving(false);
    }
  };

  const loadHistory = useCallback(
    async (item: MemoryItem, nextPage: number) => {
      setHistoryLoading(true);
      try {
        const data = await listMemoryHistory(props.userId, item.id, {
          page: nextPage,
          page_size: 15
        });
        setHistory(data.items);
        setHistoryTotal(data.total);
        setHistoryPage(nextPage);
      } catch {
        setActionError(t('user.memory.errorFallback'));
      } finally {
        setHistoryLoading(false);
      }
    },
    [props.userId, t]
  );

  const loadConflicts = async () => {
    setConflictLoading(true);
    try {
      const data = await listMemoryConflicts(props.userId, { page: 1, page_size: 20 });
      setConflicts(data.items);
      setConflictTotal(data.total);
    } catch {
      setActionError(t('user.memory.errorFallback'));
    } finally {
      setConflictLoading(false);
    }
  };

  const resolveConflict = async (item: MemoryConflictItem, accept: boolean) => {
    setActionError(null);
    try {
      await commandMemory(
        props.userId,
        {
          action: accept ? 'RESOLVE' : 'REJECT',
          candidate_id: item.id,
          expected_version: item.base_version
        },
        newRequestId()
      );
      await loadConflicts();
      await load();
      props.onMutated?.();
    } catch {
      setActionError(t('user.memory.errorFallback'));
    }
  };

  if (failed) return <ErrorState onRetry={() => void load()} />;
  if (loading && items.length === 0) return <Spin spinning />;

  return (
    <div className="user-memory-tab">
      <div className="detail-hint user-memory-hint">{t('user.memory.hint')}</div>
      {actionError ? (
        <div className="detail-hint" data-testid="memory-action-error">
          {actionError}
        </div>
      ) : null}
      {props.canManage ? (
        <div className="user-memory-toolbar">
          <div className="user-memory-toolbar-start">
            <Button data-testid="memory-create" icon={<IconPlus />} onClick={() => setCreateVisible(true)}>
              {t('user.memory.add')}
            </Button>
          </div>
          <div className="user-memory-toolbar-end">
            <span className="user-memory-recycle">
              {t('user.memory.recycleBin')}
              <Switch checked={recycle} onChange={setRecycle} size="small" />
            </span>
            <Button
              theme="borderless"
              onClick={() => {
                setConflictsVisible(true);
                void loadConflicts();
              }}
            >
              {t('user.memory.conflicts')}
            </Button>
            <ConfirmAction
              title={t('user.memory.clear')}
              onConfirm={() => void runCommand({ action: 'CLEAR' })}
            >
              <Button data-testid="memory-clear" type="danger" theme="borderless">
                {t('user.memory.clear')}
              </Button>
            </ConfirmAction>
          </div>
        </div>
      ) : null}
      <DataTable<MemoryItem>
        className="app-table-fit user-memory-table"
        collapseActions={false}
        size="small"
        dataSource={items}
        rowKey="id"
        pagination={false}
        empty={<EmptyState title={t('user.memory.empty')} />}
        columns={[
          {
            title: t('user.memory.content'),
            dataIndex: 'title',
            width: 120,
            render: (_value: string, item: MemoryItem) => (
              <Button
                theme="borderless"
                disabled={!props.canManage || item.status === 'DELETED'}
                onClick={() => {
                  setEditing(item);
                  editFormApi.current?.setValues({
                    title: item.title,
                    summary: item.summary,
                    content: formatMemoryContent(item.content)
                  });
                }}
              >
                <TableText title={formatMemoryContent(item.content)}>
                  {`${item.title}：${item.summary || formatMemoryContent(item.content)}`}
                </TableText>
              </Button>
            )
          },
          {
            title: t('user.memory.categoryLabel'),
            dataIndex: 'memory_kind',
            width: 104,
            render: (value: string, item: MemoryItem) => (
              <TableText>{`${t(`user.memory.kind.${value}`)}/${item.predicate}`}</TableText>
            )
          },
          {
            title: t('user.memory.source'),
            dataIndex: 'author',
            width: 144,
            render: (value: string) => (
              <TableText title={value}>{authorLabel(t, value)}</TableText>
            )
          },
          {
            title: t('user.memory.statusLabel'),
            dataIndex: 'status',
            width: 140,
            render: (value: string) => <TableText>{t(`user.memory.status.${value}`)}</TableText>
          },
          {
            title: t('user.memory.updated'),
            dataIndex: 'update_time',
            width: 176,
            render: (value: string) => <DateTimeText value={value} />
          },
          {
            key: 'actions',
            title: t('user.columns.action'),
            fixed: 'right' as const,
            width: 196,
            className: 'table-actions-cell',
            render: (_value: unknown, item: MemoryItem) => (
              <div className="memory-row-actions">
                <Button
                  theme="borderless"
                  size="small"
                  onClick={() => {
                    setHistoryOf(item);
                    void loadHistory(item, 1);
                  }}
                >
                  {t('user.memory.history')}
                </Button>
                {props.canManage && item.status !== 'DELETED' ? (
                  <>
                    <Button
                      theme="borderless"
                      size="small"
                      onClick={() =>
                        void runCommand({
                          action: item.status === 'ACTIVE' ? 'DISABLE' : 'ENABLE',
                          memory_id: item.id,
                          expected_version: item.version
                        })
                      }
                    >
                      {item.status === 'ACTIVE' ? t('user.memory.disable') : t('user.memory.enable')}
                    </Button>
                    <ConfirmAction
                      type="danger"
                      size="small"
                      title={t('user.memory.confirmDelete')}
                      onConfirm={() =>
                        void runCommand({
                          action: 'DELETE',
                          memory_id: item.id,
                          expected_version: item.version
                        })
                      }
                    >
                      {t('user.memory.delete')}
                    </ConfirmAction>
                  </>
                ) : null}
                {props.canManage && item.status === 'DELETED' ? (
                  <ConfirmAction
                    size="small"
                    title={t('user.memory.confirmRestore')}
                    onConfirm={() =>
                      void runCommand({
                        action: 'RESTORE',
                        memory_id: item.id,
                        expected_version: item.version
                      })
                    }
                  >
                    {t('user.memory.restore')}
                  </ConfirmAction>
                ) : null}
              </div>
            )
          }
        ]}
      />
      <PaginationFooter
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
      />
      <FormModal
        visible={createVisible}
        title={t('user.memory.createTitle')}
        okText={t('user.memory.add')}
        confirmLoading={creating}
        onOk={() => formApi.current?.submitForm()}
        onCancel={() => setCreateVisible(false)}
        getFormApi={(api) => {
          formApi.current = api as FormApi<CreateValues>;
        }}
        onSubmit={(values) => void submitCreate(values as unknown as CreateValues)}
      >
        <Form.Input field="title" label={t('user.memory.title')} rules={[{ required: true }]} />
        <Form.Input field="predicate" label={t('user.memory.categoryLabel')} initValue="general" />
        <Form.TextArea field="content" label={t('user.memory.content')} rules={[{ required: true }]} />
        <Form.Input field="tags" label="tags" />
      </FormModal>
      <FormModal
        visible={editing !== null}
        title={t('user.memory.editTitle')}
        okText={t('common.save')}
        confirmLoading={saving}
        onOk={() => editFormApi.current?.submitForm()}
        onCancel={() => setEditing(null)}
        getFormApi={(api) => {
          editFormApi.current = api as FormApi<EditValues>;
        }}
        onSubmit={(values) => void submitEdit(values as unknown as EditValues)}
      >
        <Form.Input field="title" label={t('user.memory.title')} rules={[{ required: true }]} />
        <Form.Input field="summary" label={t('user.memory.summary')} />
        <Form.TextArea field="content" label={t('user.memory.content')} rules={[{ required: true }]} />
      </FormModal>
      <SideSheet
        title={t('user.memory.conflictsTitle')}
        visible={conflictsVisible}
        onCancel={() => setConflictsVisible(false)}
        width={480}
      >
        <Spin spinning={conflictLoading}>
          {conflicts.length === 0 ? (
            <EmptyState title={t('user.memory.conflictsEmpty')} />
          ) : (
            <DataTable<MemoryConflictItem>
              dataSource={conflicts}
              rowKey="id"
              pagination={false}
              columns={[
                {
                  title: t('user.memory.content'),
                  dataIndex: 'title',
                  render: (value: string, item: MemoryConflictItem) => (
                    <TableText>{`${value}：${item.summary || formatMemoryContent(item.content)}`}</TableText>
                  )
                },
                {
                  title: t('user.memory.categoryLabel'),
                  dataIndex: 'predicate'
                },
                {
                  title: t('user.memory.statusLabel'),
                  width: 112,
                  render: (_value: unknown, item: MemoryConflictItem) => (
                    <div className="memory-row-actions">
                      <Button
                        theme="borderless"
                        size="small"
                        onClick={() => void resolveConflict(item, true)}
                      >
                        {t('user.memory.accept')}
                      </Button>
                      <Button
                        theme="borderless"
                        size="small"
                        onClick={() => void resolveConflict(item, false)}
                      >
                        {t('user.memory.reject')}
                      </Button>
                    </div>
                  )
                }
              ]}
            />
          )}
          <span className="detail-hint">{`${conflicts.length}/${conflictTotal}`}</span>
        </Spin>
      </SideSheet>
      <SideSheet
        title={t('user.memory.historyTitle')}
        visible={historyOf !== null}
        onCancel={() => setHistoryOf(null)}
        width={480}
        closeOnEsc
      >
        <Spin spinning={historyLoading}>
          {history.length === 0 ? (
            <EmptyState title={t('user.memory.historyEmpty')} />
          ) : (
            <DataTable<MemoryHistoryItem>
              className="app-table-fit memory-history-table"
              collapseActions={false}
              dataSource={history}
              rowKey="id"
              pagination={false}
              columns={[
                { title: t('user.memory.version'), dataIndex: 'version', width: 72 },
                {
                  title: t('user.memory.changeReason'),
                  dataIndex: 'change_reason',
                  render: (value: string) => (
                    <TableText>{t(`user.memory.changeReason.${value}`)}</TableText>
                  )
                },
                {
                  title: t('user.memory.actor'),
                  dataIndex: 'actor_type',
                  render: (value: string) => <TableText>{t(`user.memory.actor.${value}`)}</TableText>
                },
                {
                  title: t('user.memory.historyTime'),
                  dataIndex: 'create_time',
                  render: (value: string) => <DateTimeText value={value} />
                }
              ]}
            />
          )}
          <PaginationFooter
            page={historyPage}
            pageSize={15}
            total={historyTotal}
            onPageChange={(next) => {
              if (historyOf !== null) void loadHistory(historyOf, next);
            }}
            onPageSizeChange={() => undefined}
          />
        </Spin>
      </SideSheet>
    </div>
  );
}
