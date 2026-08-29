/**
 * X403 智能体详情/发起（TASK-006）：能力展示 + 发起对话（跳转 /chat，
 * agentId 经 location state 透传——TASK-011 衔接对话上下文）。
 */
import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { Button, Empty, Skeleton, Tag, Typography } from "@douyinfe/semi-ui";
import { IconComment } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../components/ErrorBanner";
import type { ChatApi, WorkspaceAgent } from "../types/chat";

interface AgentDetailPageProps {
  readonly api: ChatApi;
}

export function AgentDetailPage({ api }: AgentDetailPageProps) {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [agent, setAgent] = useState<WorkspaceAgent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listAgents()
      .then((items) => {
        if (!active) return;
        setAgent(items.find((item) => item.agentId === agentId) ?? null);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, agentId, reloadKey]);

  const startChat = (): void => {
    navigate("/chat", { state: { agentId }, replace: true });
    void location;
  };

  return (
    <section aria-label="智能体详情" className="agent-detail">
      <Typography.Title heading={3}>智能体详情</Typography.Title>
      {error !== null ? (
        <ErrorBanner
          message={`加载失败：${error}`}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : agent === null ? (
        <div aria-label="智能体详情加载中">
          <Skeleton.Title />
        </div>
      ) : (
        <>
          <Typography.Title heading={5}>{agent.displayName}</Typography.Title>
          <Typography.Text type="tertiary">{agent.description}</Typography.Text>
          <div className="agent-capabilities">
            {agent.capabilities.length === 0 ? (
              <Empty description="暂无能力说明" />
            ) : (
              agent.capabilities.map((capability) => (
                <Tag key={capability} color="cyan">
                  {capability}
                </Tag>
              ))
            )}
          </div>
          <Button
            aria-label="发起对话"
            disabled={!agent.available}
            icon={<IconComment />}
            onClick={startChat}
            theme="solid"
            type="primary"
          >
            发起对话
          </Button>
        </>
      )}
    </section>
  );
}
