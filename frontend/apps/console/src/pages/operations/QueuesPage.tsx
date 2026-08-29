/** C407 队列运营视图（TASK-014）：⛳依赖缺口 in-memory 先行；四态齐全。 */
import { useEffect, useState } from "react";

import { Skeleton, Tag } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { QueuesPanel } from "../../components/operations/QueuesPanel";
import { PageHeader } from "../../components/PageHeader";
import type { ConsoleApi, WorkflowQueueSummary } from "../../types/console";

interface QueuesPageProps {
  readonly api: ConsoleApi;
}

export function QueuesPage({ api }: QueuesPageProps) {
  const [queues, setQueues] = useState<readonly WorkflowQueueSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listQueues()
      .then((items) => {
        if (active) setQueues(items);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <div className="page-stack">
      <PageHeader description="工作流队列状态与积压（Phase 3 运营视图）" title="工作流队列" />
      {api.dataSource === "in-memory" && (
        <Tag color="blue" size="small">
          示例数据（in-memory，后端未就绪）
        </Tag>
      )}
      {error !== null ? (
        <ErrorBanner message={`加载失败：${error}`} onRetry={() => setReloadKey((key) => key + 1)} />
      ) : queues === null ? (
        <div aria-label="队列加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <QueuesPanel queues={queues} />
      )}
    </div>
  );
}
