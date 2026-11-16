/**
 * User-memory v2 governance tab: list, create, edit, enable/disable, delete,
 * restore from the recycle bin and read version history.
 */

import { Button, Form, Radio, SideSheet, Spin, Switch } from '@douyinfe/semi-ui';
import type { FormApi } from '@douyinfe/semi-ui/lib/es/form';
import {
  IconDelete,
  IconHistory,
  IconPause,
  IconPlay,
  IconPlus,
  IconUndo
} from '@douyinfe/semi-icons';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { apiErrorBody, newRequestId } from '../../api/client';
import i18n from '../../i18n';
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
import {
  MEMORY_PREDICATE_MAP,
  MEMORY_PREDICATES,
  SCOPE_VALUES,
  predicateLabel,
  type ScopeDimension
} from './memoryTaxonomy';

const DEFAULT_PAGE_SIZE = 15;

export interface MemoryTabProps {
  userId: string;
  canManage: boolean;
  onMutated?(): void;
}

interface CreateValues {
  title: string;
  predicate: string;
  scopeState: 'ABSENT' | 'EXPLICIT';
  audience?: string;
  region?: string;
  summary?: string;
  content: string;
  tags?: string;
  expiresAt?: Date;
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

function contentValue(item: MemoryItem): string {
  if (item.memory_kind === 'NOTE') {
    const text = item.content.text;
    return typeof text === 'string' ? text : formatMemoryContent(item.content);
  }
  return formatMemoryContent(item.content);
}

/** 仅展示该 predicate 真正支持的 scope 维度；维度内外值来自受控词表。 */
function ScopeSelect(props: {
  field: string;
  label: string;
  dimension: ScopeDimension;
  predicate: string;
  locale: string;
}) {
  const supported = MEMORY_PREDICATE_MAP.get(props.predicate)?.scopeDimensions.includes(props.dimension);
  if (supported !== true) return null;
  const { t } = useTranslation();
  return (
    <Form.Select
      field={props.field}
      label={props.label}
      optionList={SCOPE_VALUES[props.dimension].map((value) => ({
        value,
        label: t(`user.memory.scope.${props.dimension}.${value}`)
      }))}
      helpText={t('user.memory.form.scopeDimensionHint')}
    />
  );
}

/** 记忆命令错误码 → 本地化文案键；未登记的错误码一律回落通用失败。 */
function memoryErrorText(t: TFunction, error: unknown): string {
  const code = apiErrorBody(error)?.code;
  if (code !== undefined && i18n.exists(`user.memory.error.${code}`)) {
    return t(`user.memory.error.${code}`);
  }
  return t('user.memory.errorFallback');
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
  const [createPredicate, setCreatePredicate] = useState('general');
  const [createScopeState, setCreateScopeState] = useState<'ABSENT' | 'EXPLICIT'>('ABSENT');
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
    } catch (error) {
      setActionError(memoryErrorText(t, error));
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



  /** 表单只选 predicate 与单值内容：memory_kind 由 taxonomy 派生，内容写法随 kind 归一。 */
  const submitCreate = async (values: CreateValues) => {
    setCreating(true);
    setActionError(null);
    try {
      const spec = MEMORY_PREDICATE_MAP.get(values.predicate);
      if (spec === undefined) {
        throw new Error('unknown predicate');
      }
      const scope: Record<string, string> = {};
      if (values.scopeState === 'EXPLICIT') {
        if (spec.scopeDimensions.includes('audience') && values.audience) {
          scope.audience = values.audience;
        }
        if (spec.scopeDimensions.includes('region') && values.region) {
          scope.region = values.region;
        }
        if (Object.keys(scope).length === 0) {
          setActionError(t('user.memory.form.scopeRequired'));
          return;
        }
      }
      const tags = (values.tags ?? '')
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean);
      await commandMemory(
        props.userId,
        {
          action: 'CREATE',
          candidate: {
            predicate: spec.key,
            scope_state: values.scopeState,
            scope,
            content: spec.kind === 'NOTE' ? { text: values.content } : { value: values.content },
            title: values.title,
            summary: values.summary ?? '',
            tags,
            expires_at: values.expiresAt ? values.expiresAt.toISOString() : null
          }
        },
        newRequestId()
      );
      setCreateVisible(false);
      setCreatePredicate('general');
      setCreateScopeState('ABSENT');
      formApi.current?.reset();
      await load();
      props.onMutated?.();
    } catch (error) {
      setActionError(memoryErrorText(t, error));
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
            // 身份字段不可改；单值内容按本行 kind 的写法回写（NOTE=text，其余=value）。
            content:
              editing.memory_kind === 'NOTE'
                ? { ...editing.content, text: values.content }
                : { ...editing.content, value: values.content }
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
      } catch (error) {
        setActionError(memoryErrorText(t, error));
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
    } catch (error) {
      setActionError(memoryErrorText(t, error));
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
    } catch (error) {
      setActionError(memoryErrorText(t, error));
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
            <span className="user-memory-recycle" title={t('user.memory.recycleHint')}>
              {t('user.memory.recycleBin')}
              <Switch checked={recycle} onChange={setRecycle} size="small" />
            </span>
            <Button
              theme="borderless"
              title={t('user.memory.conflictsHint')}
              onClick={() => {
                setConflictsVisible(true);
                void loadConflicts();
              }}
            >
              {t('user.memory.conflicts')}
            </Button>
          </div>
          <div className="user-memory-toolbar-end">
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
            width: 232,
            render: (_value: string, item: MemoryItem) => (
              <Button
                theme="borderless"
                disabled={!props.canManage || item.status === 'DELETED'}
                onClick={() => setEditing(item)}
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
            width: 120,
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
            width: 88,
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
            width: 120,
            className: 'table-actions-cell',
            render: (_value: unknown, item: MemoryItem) => (
              <div className="memory-row-actions">
                <Button
                  theme="borderless"
                  size="small"
                  icon={<IconHistory />}
                  aria-label={t('user.memory.history')}
                  title={t('user.memory.history')}
                  onClick={() => {
                    setHistoryOf(item);
                    void loadHistory(item, 1);
                  }}
                />
                {props.canManage && item.status !== 'DELETED' ? (
                  <>
                    <Button
                      theme="borderless"
                      size="small"
                      icon={item.status === 'ACTIVE' ? <IconPause /> : <IconPlay />}
                      aria-label={
                        item.status === 'ACTIVE' ? t('user.memory.disable') : t('user.memory.enable')
                      }
                      title={
                        item.status === 'ACTIVE' ? t('user.memory.disable') : t('user.memory.enable')
                      }
                      onClick={() =>
                        void runCommand({
                          action: item.status === 'ACTIVE' ? 'DISABLE' : 'ENABLE',
                          memory_id: item.id,
                          expected_version: item.version
                        })
                      }
                    />
                    <ConfirmAction
                      type="danger"
                      size="small"
                      icon={<IconDelete />}
                      ariaLabel={t('user.memory.delete')}
                      title={t('user.memory.confirmDelete')}
                      onConfirm={() =>
                        void runCommand({
                          action: 'DELETE',
                          memory_id: item.id,
                          expected_version: item.version
                        })
                      }
                    />
                  </>
                ) : null}
                {props.canManage && item.status === 'DELETED' ? (
                  <ConfirmAction
                    size="small"
                    icon={<IconUndo />}
                    ariaLabel={t('user.memory.restore')}
                    title={t('user.memory.confirmRestore')}
                    onConfirm={() =>
                      void runCommand({
                        action: 'RESTORE',
                        memory_id: item.id,
                        expected_version: item.version
                      })
                    }
                  />
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
        key={createVisible ? 'memory-create-open' : 'memory-create-closed'}
        visible={createVisible}
        title={t('user.memory.createTitle')}
        okText={t('user.memory.add')}
        confirmLoading={creating}
        initValues={{ predicate: 'general', scopeState: 'ABSENT' }}
        onOk={() => formApi.current?.submitForm()}
        onCancel={() => {
          setCreateVisible(false);
          setCreatePredicate('general');
          setCreateScopeState('ABSENT');
        }}
        getFormApi={(api) => {
          formApi.current = api as FormApi<CreateValues>;
        }}
        onSubmit={(values) => void submitCreate(values as unknown as CreateValues)}
      >
        <Form.Select
          field="predicate"
          label={t('user.memory.form.predicate')}
          rules={[{ required: true }]}
          optionList={MEMORY_PREDICATES.map((item) => ({
            value: item.key,
            label: predicateLabel(item, i18n.language)
          }))}
          onChange={(value) => {
            const key = String(value);
            setCreatePredicate(key);
            if ((MEMORY_PREDICATE_MAP.get(key)?.scopeDimensions.length ?? 0) === 0) {
              setCreateScopeState('ABSENT');
            }
          }}
          helpText={t('user.memory.form.predicateHint')}
        />
        <Form.RadioGroup
          field="scopeState"
          label={t('user.memory.form.scopeState')}
          initValue="ABSENT"
          onChange={(event) => {
            const value = String(event.target.value);
            setCreateScopeState(value === 'EXPLICIT' ? 'EXPLICIT' : 'ABSENT');
          }}
          helpText={t('user.memory.form.scopeHint')}
        >
          <Radio value="ABSENT">{t('user.memory.form.scopeAbsent')}</Radio>
          <Radio
            value="EXPLICIT"
            disabled={(MEMORY_PREDICATE_MAP.get(createPredicate)?.scopeDimensions.length ?? 0) === 0}
          >
            {t('user.memory.form.scopeExplicit')}
          </Radio>
        </Form.RadioGroup>
        {createScopeState === 'EXPLICIT' ? (
          <>
            <ScopeSelect
              field="audience"
              label={t('user.memory.form.audience')}
              dimension="audience"
              predicate={createPredicate}
              locale={i18n.language}
            />
            <ScopeSelect
              field="region"
              label={t('user.memory.form.region')}
              dimension="region"
              predicate={createPredicate}
              locale={i18n.language}
            />
          </>
        ) : null}
        <Form.Input field="title" label={t('user.memory.title')} rules={[{ required: true }]} />
        <Form.Input field="summary" label={t('user.memory.summary')} />
        <Form.TextArea
          field="content"
          label={t('user.memory.content')}
          rules={[{ required: true }]}
          placeholder={t(
            MEMORY_PREDICATE_MAP.get(createPredicate)?.kind === 'NOTE'
              ? 'user.memory.form.contentHint.text'
              : 'user.memory.form.contentHint.value'
          )}
        />
        <Form.Input field="tags" label={t('user.memory.tags')} helpText={t('user.memory.form.tagsHint')} />
        <Form.DatePicker
          field="expiresAt"
          label={t('user.memory.form.expiresAt')}
          helpText={t('user.memory.form.expiresAtHint')}
        />
      </FormModal>
      <FormModal
        key={editing?.id ?? 'memory-edit-closed'}
        visible={editing !== null}
        title={t('user.memory.editTitle')}
        okText={t('common.save')}
        confirmLoading={saving}
        initValues={
          editing === null
            ? undefined
            : {
                title: editing.title,
                summary: editing.summary,
                content: contentValue(editing)
              }
        }
        onOk={() => editFormApi.current?.submitForm()}
        onCancel={() => setEditing(null)}
        getFormApi={(api) => {
          editFormApi.current = api as FormApi<EditValues>;
        }}
        onSubmit={(values) => void submitEdit(values as unknown as EditValues)}
      >
        {editing === null ? null : (
          <div className="detail-hint">
            {t('user.memory.form.identityReadonly', {
              kind: t(`user.memory.kind.${editing.memory_kind}`),
              predicate: editing.predicate
            })}
          </div>
        )}
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
