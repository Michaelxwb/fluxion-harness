/**
 * X403 智能体目录（TASK-006 / FEAT-P4-03）：AgentDefinition 产品模型展示
 * （名称/描述/能力/可用性，不暴露 RuntimeProfile 等底层字段）。四态齐全。
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Skeleton, Typography } from "@douyinfe/semi-ui";

import { AgentCardList } from "../components/AgentCardList";
import { ErrorBanner } from "../components/ErrorBanner";
import type { ChatApi, WorkspaceAgent } from "../types/chat";

interface AgentsPageProps {
  readonly api: ChatApi;
}

export function AgentsPage({ api }: AgentsPageProps) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<readonly WorkspaceAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listAgents()
      .then((items) => {
        if (active) setAgents(items);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  return (
    <section aria-label="智能体" className="agents-page">
      <Typography.Title heading={3}>智能体</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : agents === null ? (
        <div aria-label="智能体目录加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <AgentCardList agents={agents} onSelect={(agentId) => navigate(`/agents/${agentId}`)} />
      )}
    </section>
  );
}
