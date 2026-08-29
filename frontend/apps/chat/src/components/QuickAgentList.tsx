/** 展示组件：常用智能体卡片列表（props 只读，选择事件上抛）。 */
import { Button, Empty, Tag, Typography } from "@douyinfe/semi-ui";

import type { WorkspaceAgent } from "../types/chat";

interface QuickAgentListProps {
  readonly agents: readonly WorkspaceAgent[];
  readonly onSelect: (agentId: string) => void;
}

export function QuickAgentList({ agents, onSelect }: QuickAgentListProps) {
  return (
    <section aria-label="常用智能体" className="quick-agents">
      <Typography.Title heading={5}>常用智能体</Typography.Title>
      {agents.length === 0 ? (
        <Empty description="暂无常用智能体" />
      ) : (
        <ul className="agent-cards">
          {agents.map((agent) => (
            <li key={agent.agentId}>
              <Button
                aria-label={agent.displayName}
                className="agent-card"
                disabled={!agent.available}
                onClick={() => onSelect(agent.agentId)}
                theme="light"
              >
                <span className="agent-name">{agent.displayName}</span>
                <span className="agent-desc">{agent.description}</span>
                {agent.available ? null : <Tag color="grey">暂不可用</Tag>}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
