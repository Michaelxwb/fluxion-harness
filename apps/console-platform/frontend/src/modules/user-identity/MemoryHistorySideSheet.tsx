/**
 * 记忆版本历史 SideSheet（design §3.7 API 与 Console）：
 *
 * - 只读分页读取 `GET /users/{id}/memory/{memory_key}/history`（后端按 version DESC 排序）；
 * - 每行展示版本、变更原因（`memoryChangeReasonLabel`，含删除原因）、操作者
 *   （`memoryActorLabel` + actor_id）与变更时间——author（来源）与 actor（这次修改由谁执行）
 *   分开显示，`command_id`/`scope_revision` 等实现术语不进入界面；
 * - 复用公共 `DetailSideSheet` 与 `PaginationFooter`；作为最上层详情壳显式开启 `closeOnEsc`
 *   （下层用户详情保持关闭，ESC 只关闭当前面板）。
 */

import { Spin, Tabs } from '@douyinfe/semi-ui';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { DataTable } from '../../components/common/DataTable';
import { DateTimeText } from '../../components/common/DateTimeText';
import { DetailSideSheet } from '../../components/common/DetailSideSheet';
import { EmptyState } from '../../components/common/EmptyState';
import { ErrorState } from '../../components/common/ErrorState';
import { PaginationFooter } from '../../components/common/PaginationFooter';
import { TableText } from '../../components/common/TableText';
import { memoryActorLabel, memoryChangeReasonLabel } from './memoryOptions';
import type { MemoryHistoryItem } from './memoryTypes';
import { listMemoryHistory } from './services/users';

const HISTORY_PAGE_SIZE = 15;

export interface MemoryHistorySideSheetProps {
  userId: string;
  /** 目标记忆键；null 时关闭（不请求）。 */
  memoryKey: string | null;
  onCancel(): void;
}

export function MemoryHistorySideSheet(props: MemoryHistorySideSheetProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<MemoryHistoryItem[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestSeq = useRef(0);

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
  }, [props.memoryKey]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <DetailSideSheet
      visible={props.memoryKey !== null}
      title={t('user.memory.historyTitle')}
      subtitle={props.memoryKey ?? undefined}
      onCancel={props.onCancel}
      closeOnEsc
    >
      <Tabs.TabPane itemKey="history" tab={t('user.memory.history')}>
        <div className="detail-section-title">{t('user.memory.historyTitle')}</div>
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
