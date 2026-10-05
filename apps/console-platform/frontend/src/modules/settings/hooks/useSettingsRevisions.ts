/**
 * 版本历史状态机（前端设计 §3.5、§3.6）。
 *
 * 分页列表与回滚都只经 service 层：读取失败保留已加载行（`failed` 供面板渲染），回滚成功后由
 * 调用方重新加载当前设置（`onRestored`）。回滚本身不做任何本地状态收敛——服务端产生新版本，
 * 一切以重新读取为准。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Toast } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

import { listSettingsRevisions, restoreSettingsRevision } from '../services/settingsApi';
import type { SettingsRevision } from '../types';

export const REVISION_PAGE_SIZE_DEFAULT = 20;

export interface SettingsRevisionsState {
  items: SettingsRevision[];
  page: number;
  pageSize: number;
  total: number;
  loading: boolean;
  failed: boolean;
  restoring: boolean;
  reload(): void;
  setPage(page: number, pageSize: number): void;
  restore(revision: number): Promise<void>;
}

export function useSettingsRevisions(): SettingsRevisionsState {
  const { t } = useTranslation();
  const [items, setItems] = useState<SettingsRevision[]>([]);
  const [page, setPageState] = useState(1);
  const [pageSize, setPageSize] = useState(REVISION_PAGE_SIZE_DEFAULT);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const requestSeq = useRef(0);

  const load = useCallback(async (targetPage: number, targetSize: number) => {
    const current = ++requestSeq.current;
    setLoading(true);
    try {
      const result = await listSettingsRevisions({ page: targetPage, pageSize: targetSize });
      if (current !== requestSeq.current) {
        return;
      }
      setItems(result.items);
      setPageState(result.page);
      setPageSize(result.pageSize);
      setTotal(result.total);
      setFailed(false);
    } catch {
      if (current === requestSeq.current) {
        // 读取失败保留已加载行：面板就地给错误提示与重试，不整页顶掉历史。
        setFailed(true);
      }
    } finally {
      if (current === requestSeq.current) {
        setLoading(false);
      }
    }
  }, []);

  const setPage = useCallback((nextPage: number, nextSize: number) => {
    void load(nextPage, nextSize);
  }, [load]);

  const reload = useCallback(() => {
    void load(page, pageSize);
  }, [load, page, pageSize]);

  const restore = useCallback(
    async (revision: number) => {
      if (restoring) {
        return;
      }
      setRestoring(true);
      try {
        const result = await restoreSettingsRevision(revision);
        Toast.success({ content: t('settings.history.restored', { revision: result.revision }) });
        await load(1, pageSize);
      } catch {
        // 失败提示由 api 客户端拦截器给出（含 404 版本不存在 / 409 冲突 / 校验失败）；不回滚本地状态。
      } finally {
        setRestoring(false);
      }
    },
    [restoring, load, pageSize, t]
  );

  useEffect(() => {
    void load(1, REVISION_PAGE_SIZE_DEFAULT);
  }, [load]);

  return { items, page, pageSize, total, loading, failed, restoring, reload, setPage, restore };
}
