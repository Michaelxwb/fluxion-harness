/**
 * X406 历史记录（TASK-009 / FEAT-P4-06）：对话 + 任务统一时间线容器。
 * 服务返回时间倒序（后端契约：GET /workspace/history 按 at DESC）；容器仍做防御性排序。四态齐全。
 */
import { useEffect, useState } from "react";

import { Skeleton, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../components/ErrorBanner";
import { HistoryTimeline } from "../components/HistoryTimeline";
import type { ChatApi, WorkspaceHistoryEntry } from "../types/chat";

interface HistoryPageProps {
  readonly api: ChatApi;
}

export function HistoryPage({ api }: HistoryPageProps) {
  const [entries, setEntries] = useState<readonly WorkspaceHistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listHistory()
      .then((list) => {
        if (active) {
          setEntries([...list].sort((left, right) => right.at.localeCompare(left.at)));
        }
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <section aria-label="历史" className="history-page">
      <Typography.Title heading={3}>历史</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : entries === null ? (
        <div aria-label="历史加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <HistoryTimeline entries={entries} />
      )}
    </section>
  );
}
