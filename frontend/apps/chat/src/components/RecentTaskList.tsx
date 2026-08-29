/** 展示组件：最近任务列表（props 只读，选择事件上抛）。 */
import { Button, Empty, Typography } from "@douyinfe/semi-ui";

import { TaskStatusTag } from "./TaskStatusTag";
import type { WorkspaceTask } from "../types/chat";

interface RecentTaskListProps {
  readonly tasks: readonly WorkspaceTask[];
  readonly onSelect: (taskId: string) => void;
}

export function RecentTaskList({ tasks, onSelect }: RecentTaskListProps) {
  return (
    <section aria-label="最近任务" className="recent-tasks">
      <Typography.Title heading={5}>最近任务</Typography.Title>
      {tasks.length === 0 ? (
        <Empty description="暂无任务" />
      ) : (
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.taskId}>
              <Button
                aria-label={`${task.title}`}
                className="task-row"
                onClick={() => onSelect(task.taskId)}
                theme="light"
              >
                <span className="task-title">{task.title}</span>
                <TaskStatusTag status={task.status} />
                <span className="task-progress">{task.progress}%</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
