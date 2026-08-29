/**
 * X404 任务详情（TASK-007）：运行状态/进度/结果 + 启动信息。四态齐全。
 */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { Descriptions, Skeleton, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../components/ErrorBanner";
import { TaskStatusTag } from "../components/TaskStatusTag";
import type { ChatApi, WorkspaceTask } from "../types/chat";

interface TaskDetailPageProps {
  readonly api: ChatApi;
}

const KIND_LABEL: Readonly<Record<WorkspaceTask["kind"], string>> = {
  chat: "对话",
  workflow: "工作流运行"
};

export function TaskDetailPage({ api }: TaskDetailPageProps) {
  const { taskId } = useParams<{ taskId: string }>();
  const [task, setTask] = useState<WorkspaceTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .getTask(taskId ?? "")
      .then((item) => {
        if (active) setTask(item);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, taskId, reloadKey]);

  return (
    <section aria-label="任务详情" className="task-detail">
      <Typography.Title heading={3}>任务详情</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : task === null ? (
        <div aria-label="任务详情加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <>
          <Typography.Title heading={5}>{task.title}</Typography.Title>
          <Descriptions
            data={[
              { key: "类型", value: KIND_LABEL[task.kind] },
              {
                key: "状态",
                value: <TaskStatusTag status={task.status} />
              },
              { key: "进度", value: `${task.progress}%` },
              { key: "启动时间", value: task.startedAt },
              { key: "更新时间", value: task.updatedAt },
              { key: "结果", value: task.result ?? "—" }
            ]}
          />
        </>
      )}
    </section>
  );
}
