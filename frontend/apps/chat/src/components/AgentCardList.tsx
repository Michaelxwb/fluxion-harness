/** 展示组件：智能体目录卡片列表（props 只读，选择事件上抛）。 */
import { Button, Empty, Tag } from "@douyinfe/semi-ui";

import type { WorkspaceAgent } from "../types/chat";

interface AgentCardListProps {
  readonly agents: readonly WorkspaceAgent[];
  readonly onSelect: (agentId: string) => void;
}

export function AgentCardList({ agents, onSelect }: AgentCardListProps) {
  return (
    <section aria-label="智能体目录" className="agent-list">
      {agents.length === 0 ? (
        <Empty description="暂无可用智能体" />
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
                <span className="agent-capabilities">
                  {agent.capabilities.map((capability) => (
                    <Tag key={capability} color="cyan">
                      {capability}
                    </Tag>
                  ))}
                </span>
                {agent.available ? null : <Tag color="grey">暂不可用</Tag>}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
