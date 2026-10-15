/**
 * 用户记忆 Tab：管理动作、状态与版本历史（design §3.7 API 与 Console）。
 *
 * 契约：
 * - 列表只读投影（`GET /users/{id}/memory`）：内容/类别/来源/状态/有效期/更新时间；已删除的
 *   条目不出现在列表，版本历史仍可按 key 回查（`MemoryHistorySideSheet`）；
 * - 写操作全部经 `services/users.ts` 并带调用方幂等键与读到的 `expected_version`：创建
 *   （`author` 不可选）、启禁用（过期条目必须显式给新有效期）、按 id 删除、清空；
 * - 版本冲突（409 `MEMORY_VERSION_CONFLICT`）给出本地化提示与刷新动作，不静默吞错；
 * - 写入动作只在 ADMIN（`props.canManage`）下渲染；非管理员只读列表与历史。
 */

import { Button, DatePicker, Form, Spin } from '@douyinfe/semi-ui';
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
import { StatusTag } from '../../components/common/StatusTag';
import { TableText } from '../../components/common/TableText';
import { actionColumn } from '../../components/common/tableLayout';
import { MemoryHistorySideSheet } from './MemoryHistorySideSheet';
import {
  memoryAuthorLabel,
  memoryCategoryLabel,
  memoryErrorLabel,
  memoryStatusLabel,
  memoryWritableCategoryOptions
} from './memoryOptions';
import type {
  MemoryContent,
  MemoryCreateInput,
  MemoryEnabledUpdateInput,
  MemoryErrorCode,
  MemoryItem,
  MemoryWritableCategory
} from './memoryTypes';
import {
  clearMemory,
  createMemory,
  deleteMemory,
  listMemory,
  memoryErrorCode,
  updateMemoryEnabled
} from './services/users';

const DEFAULT_PAGE_SIZE = 15;

export interface MemoryTabProps {
  userId: string;
  /** 当前账号是否有管理权限（ADMIN）；false 时只读列表与历史。 */
  canManage: boolean;
  /** 任一写操作成功后通知外层刷新用户计数（Tab 标签/概览卡片）。 */
  onMutated?(): void;
}

interface MemoryActionError {
  /** 已知码取对应词条，未知码落通用失败文案（不回显后端 msg）。 */
  code: MemoryErrorCode | null;
}

interface CreateFormValues {
  memory_key: string;
  category: MemoryWritableCategory;
  value: string;
  expires_at?: Date | string | null;
}

/** 记忆内容可读化：普通事实 `{ value }` 直接展示，结构化内容按值拼接。 */
export function formatMemoryContent(value: MemoryContent): string {
  const entries = Object.values(value);
  if (entries.length > 0 && entries.every((entry) => typeof entry === 'string' || typeof entry === 'number')) {
    return entries.join('；');
  }
  return JSON.stringify(value);
}

/**
 * 启禁用命令：普通启禁用只送 `enabled` + 读到的版本；过期条目重新启用必须带新的有效期
 * （Runtime 明确拒绝用过去的时间当有效期，ENABLE 也不会自动延长 TTL）。
 */
function enabledCommand(entry: MemoryItem, newExpiry?: string): MemoryEnabledUpdateInput {
  return newExpiry === undefined
    ? { enabled: entry.status !== 'ACTIVE', expected_version: entry.version }
    : { enabled: true, expires_at: newExpiry, expected_version: entry.version };
}

function createInput(values: CreateFormValues): MemoryCreateInput {
  return {
    memory_key: values.memory_key,
    category: values.category,
    value: values.value,
    expires_at: values.expires_at ? new Date(values.expires_at).toISOString() : undefined
  };
}

export function MemoryTab(props: MemoryTabProps) {
  const { t } = useTranslation();
  const canManage = props.canManage;
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [actionError, setActionError] = useState<MemoryActionError | null>(null);
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [createVisible, setCreateVisible] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [expiredTarget, setExpiredTarget] = useState<MemoryItem | null>(null);
  const [expiredExpiry, setExpiredExpiry] = useState<Date | null>(null);
  const [reenabling, setReenabling] = useState(false);
  const [reenableError, setReenableError] = useState<string | null>(null);
  const formApi = useRef<FormApi | null>(null);
  const requestSeq = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const result = await listMemory(props.userId, { page, page_size: pageSize });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
    } catch {
      if (seq !== requestSeq.current) {
        return;
      }
      setFailed(true);
      setItems([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
      }
    }
  }, [props.userId, page, pageSize]);

  useEffect(() => {
    setPage(1);
  }, [props.userId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** 写操作统一收口：成功刷新列表与计数；失败落到可见错误（版本冲突可刷新后重试）。 */
  const runAction = async (action: () => Promise<unknown>): Promise<void> => {
    setActionError(null);
    try {
      await action();
      await reload();
      props.onMutated?.();
    } catch (error) {
      setActionError({ code: memoryErrorCode(error) });
    }
  };

  const openCreate = (): void => {
    setCreateError(null);
    setCreateVisible(true);
  };

  const submitCreate = async (values: CreateFormValues): Promise<void> => {
    setCreating(true);
    setCreateError(null);
    try {
      await createMemory(props.userId, createInput(values), newRequestId());
      setCreateVisible(false);
      await reload();
      props.onMutated?.();
    } catch (error) {
      setCreateError(memoryErrorLabel(t, memoryErrorCode(error)));
    } finally {
      setCreating(false);
    }
  };

  const toggleEnabled = (entry: MemoryItem): void => {
    if (entry.status === 'EXPIRED') {
      setExpiredExpiry(null);
      setReenableError(null);
      setExpiredTarget(entry);
      return;
    }
    void runAction(() =>
      updateMemoryEnabled(props.userId, entry.memory_key, enabledCommand(entry), newRequestId())
    );
  };

  const submitReenable = async (): Promise<void> => {
    if (expiredTarget === null || expiredExpiry === null) {
      return;
    }
    setReenabling(true);
    setReenableError(null);
    try {
      await updateMemoryEnabled(
        props.userId,
        expiredTarget.memory_key,
        enabledCommand(expiredTarget, expiredExpiry.toISOString()),
        newRequestId()
      );
      setExpiredTarget(null);
      await reload();
      props.onMutated?.();
    } catch (error) {
      setReenableError(memoryErrorLabel(t, memoryErrorCode(error)));
    } finally {
      setReenabling(false);
    }
  };

  const remove = (entry: MemoryItem): void => {
    void runAction(() => deleteMemory(props.userId, entry.id, entry.version, newRequestId()));
  };

  const clearAll = (): void => {
    void runAction(() => clearMemory(props.userId, newRequestId()));
  };

  return (
    <div>
      {canManage ? (
        <div className="detail-toolbar">
          <Button theme="solid" icon={<IconPlus />} data-testid="memory-create" onClick={openCreate}>
            {t('user.memory.add')}
          </Button>
          <ConfirmAction title={t('user.common.confirmClear')} onConfirm={clearAll}>
            {t('user.memory.clear')}
          </ConfirmAction>
        </div>
      ) : null}
      {actionError ? (
        <div className="detail-hint" data-testid="memory-action-error">
          <span>{memoryErrorLabel(t, actionError.code)}</span>
          {actionError.code === 'MEMORY_VERSION_CONFLICT' ? (
            <Button
              theme="borderless"
              type="primary"
              size="small"
              data-testid="memory-refresh"
              onClick={() => {
                setActionError(null);
                void reload();
              }}
            >
              {t('common.refresh')}
            </Button>
          ) : null}
        </div>
      ) : null}
      <div className="detail-section-title">{t('user.tabs.memory')}</div>
      {loading ? (
        <Spin style={{ display: 'block', margin: '32px auto' }} />
      ) : failed ? (
        <ErrorState onRetry={() => void reload()} />
      ) : items.length === 0 ? (
        <EmptyState title={t('user.memory.empty')} />
      ) : (
        <DataTable
          className="app-table-fit user-memory-table"
          scroll={{ x: '100%' }}
          // 行内三个动作（历史/启停用/删除）在 en-US 下需要 196px 列宽（zh-CN 132px）：详情表的最小
          // 表宽（880px）始终放得下操作列，窄屏靠表内横向滚动 + 右侧固定操作列保持可见，不折叠成
          // 「更多」菜单（否则 390px 下删除动作不可达）。
          collapseActions={false}
          dataSource={items}
          rowKey="id"
          pagination={false}
          columns={[
            {
              title: t('user.memory.content'),
              dataIndex: 'content',
              width: 'auto',
              ellipsis: true,
              render: (value: MemoryContent) => <TableText>{formatMemoryContent(value)}</TableText>
            },
            {
              title: t('user.memory.categoryLabel'),
              dataIndex: 'category',
              width: 104,
              ellipsis: true,
              render: (value: string) => (
                <TableText title={value}>{memoryCategoryLabel(t, value)}</TableText>
              )
            },
            {
              title: t('user.memory.source'),
              dataIndex: 'author',
              width: 120,
              ellipsis: true,
              render: (value: string) => (
                <TableText title={value}>{memoryAuthorLabel(t, value)}</TableText>
              )
            },
            {
              title: t('user.memory.statusLabel'),
              dataIndex: 'status',
              // 状态标签 + 12px 有效期第二行实测 135px（en-US/zh-CN 同口径），152 足够且不留空白；
              // 省下的宽度留给操作列（196）与内容列（auto）。
              width: 152,
              render: (value: string, entry: MemoryItem) => (
                <div className="memory-status-cell">
                  <StatusTag
                    status={value}
                    options={{
                      ACTIVE: { color: 'green', label: memoryStatusLabel(t, 'ACTIVE') },
                      DISABLED: { color: 'grey', label: memoryStatusLabel(t, 'DISABLED') },
                      EXPIRED: { color: 'amber', label: memoryStatusLabel(t, 'EXPIRED') }
                    }}
                  />
                  {entry.expires_at ? (
                    <span className="memory-expiry" title={t('user.memory.expiresAt')}>
                      <DateTimeText value={entry.expires_at} />
                    </span>
                  ) : null}
                </div>
              )
            },
            {
              title: t('user.memory.updated'),
              dataIndex: 'update_time',
              // 时间列实测 135px（`2026-10-15 18:19:03` + 单元格内边距），152 与状态列同宽。
              width: 152,
              render: (value: string) => <DateTimeText value={value} />
            },
            {
              ...actionColumn(196),
              title: t('user.columns.action'),
              render: (_: unknown, entry: MemoryItem) => (
                <div className="memory-row-actions">
                  <Button
                    theme="borderless"
                    data-testid={`memory-history-${entry.memory_key}`}
                    onClick={() => setHistoryKey(entry.memory_key)}
                  >
                    {t('user.memory.history')}
                  </Button>
                  {canManage ? (
                    <>
                      <Button
                        theme="borderless"
                        data-testid={`memory-toggle-${entry.memory_key}`}
                        onClick={() => toggleEnabled(entry)}
                      >
                        {entry.status === 'ACTIVE'
                          ? t('user.memory.disable')
                          : t('user.memory.enable')}
                      </Button>
                      <ConfirmAction
                        title={t('user.memory.confirmDelete')}
                        testId={`memory-delete-${entry.memory_key}`}
                        onConfirm={() => remove(entry)}
                      >
                        {t('user.memory.delete')}
                      </ConfirmAction>
                    </>
                  ) : null}
                </div>
              )
            }
          ]}
        />
      )}
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
      <div className="detail-hint">{t('user.memory.hint')}</div>
      <FormModal
        visible={createVisible}
        width={520}
        title={t('user.memory.createTitle')}
        okText={t('user.memory.add')}
        confirmLoading={creating}
        onOk={() => formApi.current?.submitForm()}
        onCancel={() => setCreateVisible(false)}
        getFormApi={(api) => {
          formApi.current = api;
        }}
        onSubmit={(values) => {
          void submitCreate(values as unknown as CreateFormValues);
        }}
      >
        {createError ? (
          <div className="detail-hint" data-testid="memory-create-error">
            {createError}
          </div>
        ) : null}
        <Form.Input
          field="memory_key"
          label={t('user.memory.form.memoryKey')}
          extraText={t('user.memory.form.memoryKeyHint')}
          rules={[
            { required: true, message: t('user.memory.form.memoryKey') },
            { pattern: /^[a-z0-9][a-z0-9.-]{0,63}$/, message: t('user.memory.form.memoryKeyHint') }
          ]}
        />
        <Form.Select
          field="category"
          label={t('user.memory.form.category')}
          initValue="PREFERENCE"
          optionList={memoryWritableCategoryOptions(t)}
          rules={[{ required: true, message: t('user.memory.form.category') }]}
        />
        <Form.TextArea
          field="value"
          label={t('user.memory.content')}
          extraText={t('user.memory.form.valueHint')}
          autosize={{ minRows: 2, maxRows: 4 }}
          rules={[{ required: true, message: t('user.memory.content') }]}
        />
        <Form.DatePicker
          field="expires_at"
          label={t('user.memory.form.expiresAt')}
          type="dateTime"
          extraText={t('user.memory.form.expiresAtHint')}
          style={{ width: '100%' }}
        />
      </FormModal>
      <FormModal
        visible={expiredTarget !== null}
        width={480}
        title={t('user.memory.enableExpiredTitle')}
        okText={t('user.memory.enable')}
        confirmLoading={reenabling}
        okButtonProps={{ disabled: expiredExpiry === null }}
        onOk={() => void submitReenable()}
        onCancel={() => setExpiredTarget(null)}
      >
        {reenableError ? (
          <div className="detail-hint" data-testid="memory-reenable-error">
            {reenableError}
          </div>
        ) : null}
        <div className="detail-hint">{t('user.memory.enableExpiredHint')}</div>
        <DatePicker
          data-testid="memory-new-expiry"
          type="dateTime"
          style={{ width: '100%' }}
          placeholder={t('user.memory.form.newExpiresAt')}
          onChange={(value) => setExpiredExpiry(value instanceof Date ? value : null)}
        />
      </FormModal>
      <MemoryHistorySideSheet
        userId={props.userId}
        memoryKey={historyKey}
        onCancel={() => setHistoryKey(null)}
      />
    </div>
  );
}
