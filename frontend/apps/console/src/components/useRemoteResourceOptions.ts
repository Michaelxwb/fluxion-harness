import { useCallback, useEffect, useRef, useState } from "react";

import type { ConsoleApi, ResourceType } from "../types/console";

export interface RemoteResourceOption {
  readonly value: string;
  readonly label: string;
  readonly resourceId: string;
  readonly version: string;
}

const SEARCH_PAGE_SIZE = 100;

/** FEAT-03：资源选择器远程搜索 hook。
 *
 * 首屏与搜索均走服务端分页（每 kind 100 条）+ keyword 搜索；大数据集靠关键词
 * 到达全部记录，不再静默截断。`truncated` 为 true 时调用方必须呈现"仅显示
 * 前 N 条，请细化关键词"提示。请求序号 guard 防乱序覆盖；组件卸载取消置位。
 */
export function useRemoteResourceOptions(
  api: ConsoleApi,
  kinds: readonly ResourceType[],
  active: boolean,
  reloadSignal = 0
): {
  readonly options: readonly RemoteResourceOption[];
  readonly loading: boolean;
  readonly truncated: boolean;
  readonly onSearch: (keyword: string) => void;
} {
  const [options, setOptions] = useState<readonly RemoteResourceOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(
    async (keyword: string): Promise<void> => {
      seq.current += 1;
      const requestId = seq.current;
      setLoading(true);
      try {
        const pages = await Promise.all(
          kinds.map((kind) =>
            api.listResources(kind, {
              page: 1,
              pageSize: SEARCH_PAGE_SIZE,
              keyword: keyword.trim() || undefined
            })
          )
        );
        if (requestId !== seq.current) return;
        const merged = pages.flatMap((result, index) =>
          result.items.map((item) => ({
            value: kinds.length > 1 ? `${kinds[index]}:${item.resourceId}@${item.currentVersion}` : item.resourceId,
            label: `${item.displayName || item.resourceId}（${item.currentVersion}）`,
            resourceId: item.resourceId,
            version: item.currentVersion
          }))
        );
        const total = pages.reduce((sum, result) => sum + result.total, 0);
        setOptions(merged);
        setTruncated(total > merged.length);
      } catch {
        if (requestId !== seq.current) return;
        setOptions([]);
        setTruncated(false);
      } finally {
        if (requestId === seq.current) setLoading(false);
      }
    },
    [api, kinds.join(",")]
  );

  useEffect(() => {
    if (!active) return;
    void load("");
  }, [active, load, reloadSignal]);

  useEffect(
    () => () => {
      seq.current += 1;
      if (timer.current !== null) clearTimeout(timer.current);
    },
    []
  );

  const onSearch = useCallback(
    (keyword: string): void => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void load(keyword);
      }, 300);
    },
    [load]
  );

  return { options, loading, truncated, onSearch };
}

/** FEAT-03：平台用户选择器远程搜索（与 useRemoteResourceOptions 同语义）。 */
export function useRemoteUserOptions(
  api: ConsoleApi,
  active: boolean,
  reloadSignal = 0
): {
  readonly options: readonly RemoteResourceOption[];
  readonly loading: boolean;
  readonly truncated: boolean;
  readonly onSearch: (keyword: string) => void;
} {
  const [options, setOptions] = useState<readonly RemoteResourceOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const seq = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(
    async (keyword: string): Promise<void> => {
      seq.current += 1;
      const requestId = seq.current;
      setLoading(true);
      try {
        const result = await api.listPlatformUsers({
          page: 1,
          pageSize: 100,
          keyword: keyword.trim() || undefined
        });
        if (requestId !== seq.current) return;
        setOptions(
          result.items.map((user) => ({
            value: user.platformUserId,
            label: user.displayName
              ? `${user.displayName}（${user.platformUserId}）`
              : user.platformUserId,
            resourceId: user.platformUserId,
            version: ""
          }))
        );
        setTruncated(result.total > result.items.length);
      } catch {
        if (requestId !== seq.current) return;
        setOptions([]);
        setTruncated(false);
      } finally {
        if (requestId === seq.current) setLoading(false);
      }
    },
    [api]
  );

  useEffect(() => {
    if (!active) return;
    void load("");
  }, [active, load, reloadSignal]);

  useEffect(
    () => () => {
      seq.current += 1;
      if (timer.current !== null) clearTimeout(timer.current);
    },
    []
  );

  const onSearch = useCallback(
    (keyword: string): void => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void load(keyword);
      }, 300);
    },
    [load]
  );

  return { options, loading, truncated, onSearch };
}
