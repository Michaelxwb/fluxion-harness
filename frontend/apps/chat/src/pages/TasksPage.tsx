/**
 * X404 任务列表（TASK-007 / FEAT-P4-04）：对话/Workflow 运行统一展示。
 * 四态齐全；空态带引导入口（B-04）。
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Skeleton, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../components/ErrorBanner";
import { TaskList } from "../components/TaskList";
import type { ChatApi, WorkspaceTask } from "../types/chat";

interface TasksPageProps {
  readonly api: ChatApi;
}

export function TasksPage({ api }: TasksPageProps) {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<readonly WorkspaceTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listTasks()
      .then((items) => {
        if (active) setTasks(items);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <section aria-label="任务" className="tasks-page">
      <Typography.Title heading={3}>任务</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : tasks === null ? (
        <div aria-label="任务列表加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <TaskList tasks={tasks} onSelect={(taskId) => navigate(`/tasks/${taskId}`)} />
      )}
    </section>
  );
}
