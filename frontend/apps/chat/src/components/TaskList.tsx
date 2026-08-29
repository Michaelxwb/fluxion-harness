/** 展示组件：任务列表（对话/工作流运行统一；props 只读，选择事件上抛）。 */
import { Button, Empty } from "@douyinfe/semi-ui";
import { Link } from "react-router-dom";

import { TaskStatusTag } from "./TaskStatusTag";
import type { WorkspaceTask } from "../types/chat";

interface TaskListProps {
  readonly tasks: readonly WorkspaceTask[];
  readonly onSelect: (taskId: string) => void;
}

export function TaskList({ tasks, onSelect }: TaskListProps) {
  return (
    <section aria-label="任务列表" className="task-list">
      {tasks.length === 0 ? (
        <Empty description="暂无任务">
          <Link className="task-empty-link" to="/chat">
            去发起对话
          </Link>
        </Empty>
      ) : (
        <ul className="task-rows">
          {tasks.map((task) => (
            <li key={task.taskId}>
              <Button
                aria-label={task.title}
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
