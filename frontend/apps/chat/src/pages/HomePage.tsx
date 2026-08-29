/**
 * X402 首页（TASK-005 / FEAT-P4-02）：最近任务 + 常用智能体聚合容器。
 * 四态：loading Skeleton / empty / error ErrorBanner+重试 / success 列表。
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Skeleton, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../components/ErrorBanner";
import { QuickAgentList } from "../components/QuickAgentList";
import { RecentTaskList } from "../components/RecentTaskList";
import type { ChatApi, WorkspaceAgent, WorkspaceTask } from "../types/chat";

interface HomePageProps {
  readonly api: ChatApi;
}

export function HomePage({ api }: HomePageProps) {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<readonly WorkspaceTask[] | null>(null);
  const [agents, setAgents] = useState<readonly WorkspaceAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void Promise.all([api.listRecentTasks(), api.listAgents()])
      .then(([recentTasks, quickAgents]) => {
        if (!active) return;
        setTasks(recentTasks);
        setAgents(quickAgents);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <section aria-label="首页" className="home-page">
      <Typography.Title heading={3}>首页</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : tasks === null || agents === null ? (
        <div aria-label="首页加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <>
          <RecentTaskList tasks={tasks} onSelect={(taskId) => navigate(`/tasks/${taskId}`)} />
          <QuickAgentList agents={agents} onSelect={(agentId) => navigate(`/agents/${agentId}`)} />
        </>
      )}
    </section>
  );
}
