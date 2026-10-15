/**
 * 记忆版本历史 SideSheet（design §3.7 API 与 Console）：
 *
 * - 只读分页读取 `GET /users/{id}/memory/{memory_key}/history`（后端按 version DESC 排序）；
 * - 每行展示版本、变更原因（`memoryChangeReasonLabel`，含删除原因）、操作者
 *   （`memoryActorLabel` + actor_id）与变更时间——author（来源）与 actor（这次修改由谁执行）
 *   分开显示，`command_id`/`scope_revision` 等实现术语不进入界面；
 * - 删除后恢复（评审条目 5）：最新版本是 tombstone 时（`memoryTombstoneVersion`），ADMIN 可经
 *   确认动作恢复同一 key；保留版本随恢复命令提交，冲突（409）给本地化错误并刷新状态，可重试；
 * - 复用公共 `DetailSideSheet` 与 `PaginationFooter`；作为最上层详情壳显式开启 `closeOnEsc`
 *   （下层用户详情保持关闭，ESC 只关闭当前面板）。
 */

import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { newRequestId } from '../../api/client';
import { ConfirmAction } from '../../components/common/ConfirmAction';
import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { TableText } from '../../components/common/TableText';
import { createMemoryCommandContext } from './memoryCommands';
import { memoryTombstoneVersion } from './memoryHistory';
import { memoryActorLabel, memoryChangeReasonLabel, memoryErrorLabel } from './memoryOptions';
import type { MemoryHistoryItem } from './memoryTypes';
import { listMemoryHistory, memoryErrorCode, restoreMemory } from './services/users';

const HISTORY_PAGE_SIZE = 15;

export interface MemoryHistorySideSheetProps {
  userId: string;
  /** 目标记忆键；null 时关闭（不请求）。 */
  memoryKey: string | null;
  /** 当前账号是否有管理权限（ADMIN）；false 时不渲染恢复动作。 */
  canManage: boolean;
  onCancel(): void;
  /** 恢复成功：外层关闭面板并刷新列表（恢复的条目重新出现在列表里）。 */
  onRestored?(): void;
}

export function MemoryHistorySideSheet(props: MemoryHistorySideSheetProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<MemoryHistoryItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [tombstoneVersion, setTombstoneVersion] = useState<number | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const requestSeq = useRef(0);
  // 一次操作 = 一个命令号 + 一份输入快照：结果未明确前的重试复用同一 key（评审条目 3 同口径）。
  const commands = useRef(createMemoryCommandContext(newRequestId));

  const reload = useCallback(async () => {
    if (props.memoryKey === null) {
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setFailed(false);
    try {
      const result = await listMemoryHistory(props.userId, props.memoryKey, {
        page,
        page_size: HISTORY_PAGE_SIZE
      });
      if (seq !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setTotal(result.total);
      // 只有第一页能看到最新版本（version DESC）：删除态与保留版本只在这里更新。
      if (page === 1) {
        setTombstoneVersion(memoryTombstoneVersion(result.items));
      }
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
  }, [props.userId, props.memoryKey, page]);

  useEffect(() => {
    setPage(1);
    setTombstoneVersion(null);
    setRestoreError(null);
  }, [props.memoryKey]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** 恢复删除墓碑：保留版本来自最新历史行；失败给本地化错误并刷新状态（可重试）。 */
  const submitRestore = async (): Promise<void> => {
    if (props.memoryKey === null || tombstoneVersion === null || restoring) {
      return;
    }
    const identity = `restore:${props.userId}:${props.memoryKey}`;
    const input = { expected_version: tombstoneVersion };
    setRestoreError(null);
    setRestoring(true);
    try {
      const key = commands.current.key(identity, input);
      await restoreMemory(props.userId, props.memoryKey, input.expected_version, key);
      commands.current.release(identity);
      props.onRestored?.();
    } catch (error) {
      setRestoreError(memoryErrorLabel(t, memoryErrorCode(error)));
      await reload();
    } finally {
      setRestoring(false);
    }
  };

  return (
    <DetailSideSheet
      visible={props.memoryKey !== null}
      title={t('user.memory.historyTitle')}
      subtitle={props.memoryKey ?? undefined}
      actions={
        props.canManage && tombstoneVersion !== null ? (
          <ConfirmAction
            title={t('user.memory.confirmRestore')}
            testId={`memory-restore-${props.memoryKey}`}
            theme="solid"
            onConfirm={submitRestore}
          >
            {t('user.memory.restore')}
          </ConfirmAction>
        ) : undefined
      }
      // 单 Pane 也要给 activeKey：Semi 的 Tabs 在没有 activeKey 时所有 Pane 都是 inactive
      // （display:none + aria-hidden），历史表与分页会整体不可见、行内容读屏/角色查询也不可达。
      activeTab="history"
      onCancel={props.onCancel}
      closeOnEsc
    >
      <Tabs.TabPane itemKey="history" tab={t('user.memory.history')}>
        <div className="detail-section-title">{t('user.memory.historyTitle')}</div>
        {restoreError ? (
          <div className="detail-hint" data-testid="memory-restore-error">
            {restoreError}
          </div>
        ) : null}
        {loading ? (
          <Spin style={{ display: 'block', margin: '32px auto' }} />
        ) : failed ? (
          <ErrorState onRetry={() => void reload()} />
        ) : items.length === 0 ? (
          <EmptyState title={t('user.memory.historyEmpty')} />
        ) : (
          <DataTable
            className="app-table-fit memory-history-table"
            scroll={{ x: '100%' }}
            dataSource={items}
            rowKey="id"
            pagination={false}
            columns={[
              { title: t('user.memory.version'), dataIndex: 'version', width: 88 },
              {
                title: t('user.memory.changeReason'),
                dataIndex: 'change_reason',
                width: 'auto',
                ellipsis: true,
                render: (value: string) => (
                  <TableText title={value}>{memoryChangeReasonLabel(t, value)}</TableText>
                )
              },
              {
                title: t('user.memory.actor'),
                dataIndex: 'actor_type',
                width: 136,
                ellipsis: true,
                render: (value: string) => (
                  <TableText title={value}>{memoryActorLabel(t, value)}</TableText>
                )
              },
              {
                title: t('user.memory.actorId'),
                dataIndex: 'actor_id',
                width: 240,
                ellipsis: true,
                render: (value: string | null) => (value ? <TableText>{value}</TableText> : '-')
              },
              {
                title: t('user.memory.historyTime'),
                dataIndex: 'create_time',
                width: 176,
                render: (value: string) => <DateTimeText value={value} />
              }
            ]}
          />
        )}
        <PaginationFooter
          page={page}
          pageSize={HISTORY_PAGE_SIZE}
          total={total}
          onPageChange={setPage}
        />
      </Tabs.TabPane>
    </DetailSideSheet>
  );
}
