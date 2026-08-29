/**
 * X408 对话页路由容器（TASK-011）：迁移现有 ChatApp 对话能力（流式复用），
 * 从智能体目录进入时携带 agentId 上下文（location.state，TASK-006 发起路径）。
 */
import { useLocation } from "react-router-dom";

import { ChatApp } from "../App";
import type { ChatApi } from "../types/chat";

interface ChatPageProps {
  readonly api: ChatApi;
}

export function ChatPage({ api }: ChatPageProps) {
  const location = useLocation();
  const state = location.state as { agentId?: string } | null;
  return <ChatApp api={api} initialAgentId={state?.agentId} />;
}
