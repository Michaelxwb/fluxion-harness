/** C407 Worker 运营视图（TASK-014）：⛳依赖缺口 in-memory 先行；四态齐全。 */
import { useEffect, useState } from "react";

import { Skeleton, Tag } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { WorkersPanel } from "../../components/operations/WorkersPanel";
import { PageHeader } from "../../components/PageHeader";
import type { ConsoleApi, WorkflowWorkerSummary } from "../../types/console";

interface WorkersPageProps {
  readonly api: ConsoleApi;
}

export function WorkersPage({ api }: WorkersPageProps) {
  const [workers, setWorkers] = useState<readonly WorkflowWorkerSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listWorkers()
      .then((items) => {
        if (active) setWorkers(items);
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
      <PageHeader description="运行 Worker 状态与分摊（Phase 3 运营视图）" title="运行 Worker" />
      {api.dataSource === "in-memory" && (
        <Tag color="blue" size="small">
          示例数据（in-memory，后端未就绪）
        </Tag>
      )}
      {error !== null ? (
        <ErrorBanner message={`加载失败：${error}`} onRetry={() => setReloadKey((key) => key + 1)} />
      ) : workers === null ? (
        <div aria-label="Worker 加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <WorkersPanel workers={workers} />
      )}
    </div>
  );
}
