import { useEffect, useMemo, useState } from "react";

import { Button, Layout, Space, Spin, Tag, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconMoon, IconSend, IconSun, IconUser } from "@douyinfe/semi-icons";

import { Navigate, Route, Routes } from "react-router-dom";

import { WorkspaceLayout } from "./components/WorkspaceLayout";
import { ChatPage } from "./pages/ChatPage";
import { SettingsPage } from "./pages/SettingsPage";
import { HomePage } from "./pages/HomePage";
import { AgentsPage } from "./pages/AgentsPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { TasksPage } from "./pages/TasksPage";
import { TaskDetailPage } from "./pages/TaskDetailPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { MemoryProfilePage } from "./pages/MemoryProfilePage";
import type { ChatAccess, ChatApi, ChatRequest, ChatResultKind } from "./types/chat";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ChatAppProps {
  readonly api: ChatApi;
  /** TASK-011：从智能体目录携带的 agentId 上下文（无 access 时用于产品名解析）。 */
  readonly initialAgentId?: string;
}

/**
 * TASK-003：Workspace 路由表（design §3.2 Chat Web 路由结构）。
 * Router 实例（HashRouter/MemoryRouter）由调用方注入；`/` 重定向 `/home`。
 */
export function WorkspaceApp({ api }: ChatAppProps) {
  return (
    <Routes>
      <Route element={<WorkspaceLayout api={api} />}>
        <Route path="/" element={<Navigate replace to="/home" />} />
        <Route path="/home" element={<HomePage api={api} />} />
        <Route path="/agents" element={<AgentsPage api={api} />} />
        <Route path="/agents/:agentId" element={<AgentDetailPage api={api} />} />
        <Route path="/tasks" element={<TasksPage api={api} />} />
        <Route path="/tasks/:taskId" element={<TaskDetailPage api={api} />} />
        <Route path="/approvals" element={<ApprovalsPage api={api} />} />
        <Route path="/history" element={<HistoryPage api={api} />} />
        <Route path="/memory" element={<MemoryProfilePage api={api} />} />
        <Route path="/chat" element={<ChatPage api={api} />} />
        <Route path="/settings" element={<SettingsPage />} />
        {/* P2（review）：未知路径回首页，避免空白页 */}
        <Route path="*" element={<Navigate replace to="/home" />} />
      </Route>
    </Routes>
  );
}

interface ChatItem {
  readonly content: string;
  readonly id: string;
  readonly kind: ChatResultKind | "user" | "error";
  /** TASK-011（E-04）：error 帧的错误说明；已收内容保留在 content。 */
  readonly errorMessage?: string;
}

export function ChatApp({ api, initialAgentId }: ChatAppProps) {
  const { mode, toggle } = useThemeMode();
  const [content, setContent] = useState("");
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [platformUserId, setPlatformUserId] = useState<string>();
  const [access, setAccess] = useState<ChatAccess | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [resolvingAccess, setResolvingAccess] = useState(api.resolveAccess !== undefined);
  // closure TASK-009（P1C-05 二层）：产品名解析——失败降级占位，不暴露 raw agent_id。
  const [agentDisplayName, setAgentDisplayName] = useState("智能体");
  const [sending, setSending] = useState(false);
  // TASK-011（E-04）：最近失败消息内容，供 error 帧重试。
  const [lastFailedContent, setLastFailedContent] = useState<string | null>(null);
  const conversationId = useMemo(() => `conversation-${Date.now()}`, []);
  const requiresAccess = api.resolveAccess !== undefined;

  useEffect(() => {
    if (!api.resolveAccess) {
      // TASK-011：无 access 但携带智能体目录上下文时，解析所选智能体产品名。
      if (initialAgentId && api.getAgentProduct) {
        let active = true;
        void api
          .getAgentProduct(initialAgentId)
          .then((face) => {
            if (active) setAgentDisplayName(face?.displayName ?? "智能体");
          })
          .catch(() => {
            if (active) setAgentDisplayName("智能体");
          });
        return () => {
          active = false;
        };
      }
      return;
    }
    let active = true;
    void api
      .resolveAccess()
      .then((resolved) => {
        if (!active) return;
        setAccess(resolved);
        setPlatformUserId(resolved.platformUserId);
        setAccessError(null);
        // closure TASK-009：产品名解析——经产品 API 取 displayName，失败降级
        // 占位「智能体」，任何路径都不展示 raw agent_id。
        if (!api.getAgentProduct) return;
        void api
          .getAgentProduct(resolved.agentId)
          .then((face) => {
            if (active) setAgentDisplayName(face?.displayName ?? "智能体");
          })
          .catch(() => {
            if (active) setAgentDisplayName("智能体");
          });
      })
      .catch((cause: unknown) => {
        if (active) setAccessError(cause instanceof Error ? cause.message : "Chat 访问链接无效");
      })
      .finally(() => {
        if (active) setResolvingAccess(false);
      });
    return () => {
      active = false;
    };
  }, [api, initialAgentId]);

  async function submit(submitContent?: string): Promise<void> {
    const trimmed = (submitContent ?? content).trim();
    if (!trimmed || sending || (requiresAccess && !access)) return;
    const messageId = `message-${Date.now()}-${messages.length}`;
    const assistantId = `${messageId}-response`;
    if (submitContent === undefined) setContent("");
    setLastFailedContent(null);
    setMessages((items) => [...items, { content: trimmed, id: messageId, kind: "user" }]);
    setSending(true);
    const updateAssistant = (updater: (item: ChatItem) => ChatItem): void => {
      setMessages((items) =>
        items.map((item) => (item.id === assistantId ? updater(item) : item))
      );
    };
    try {
      if (api.sendMessageStream) {
        await submitStreaming(
          api,
          { content: trimmed, conversationId, messageId },
          assistantId,
          updateAssistant
        );
      } else {
        await submitPlain(api, { content: trimmed, conversationId, messageId }, assistantId);
      }
    } catch (error) {
      // E-04：保留已收内容，错误以 errorMessage 呈现并附重试入口。
      // P2（review）：非流式兜底（submitPlain）在 sendMessage 成功前没有 assistant
      // 占位——updateAssistant 找不到即静默 no-op。不存在则追加 error 项，保证失败可见。
      setLastFailedContent(trimmed);
      const message = error instanceof Error ? error.message : "消息发送失败";
      setMessages((items) => {
        if (!items.some((item) => item.id === assistantId)) {
          return [...items, { content: "", errorMessage: message, id: assistantId, kind: "error" }];
        }
        return items.map((item) =>
          item.id === assistantId ? { ...item, errorMessage: message, kind: "error" } : item
        );
      });
    } finally {
      setSending(false);
    }
  }

  async function submitStreaming(
    api: ChatApi,
    request: ChatRequest,
    assistantId: string,
    updateAssistant: (updater: (item: ChatItem) => ChatItem) => void
  ): Promise<void> {
    setMessages((items) => [...items, { content: "", id: assistantId, kind: "message" }]);
    let receivedOutput = false;
    let hadError = false;
    await api.sendMessageStream!(request, (event) => {
      if (event.kind === "token" && typeof event.content === "string") {
        receivedOutput = true;
        updateAssistant((item) => ({ ...item, content: item.content + event.content }));
      } else if (event.kind === "completed" && event.response) {
        receivedOutput = true;
        if (event.response.platformUserId) setPlatformUserId(event.response.platformUserId);
        updateAssistant((item) => {
          // 后端流式失败回退路径会先发若干 token 再发 completed（output 已含这些 token）。
          // 若已累加的流式内容是 output 的前缀，保留累加结果避免重复渲染；否则用完整 output。
          const streamed = item.content;
          const output = event.response!.output;
          const keepStreamed = streamed.length > 0 && output.startsWith(streamed);
          return {
            ...item,
            content: keepStreamed ? streamed : output,
            kind: event.response!.kind
          };
        });
      } else if (event.kind === "error") {
        hadError = true;
        // E-04：中断时保留已收内容，错误信息单独呈现。
        updateAssistant((item) => ({
          ...item,
          errorMessage: event.message ?? "消息发送失败",
          kind: "error"
        }));
      }
    });
    if (!receivedOutput && !hadError) {
      updateAssistant((item) => ({ ...item, errorMessage: "（无输出）", kind: "error" }));
    }
    if (hadError) setLastFailedContent(request.content);
  }

  async function submitPlain(
    api: ChatApi,
    request: ChatRequest,
    assistantId: string
  ): Promise<void> {
    const result = await api.sendMessage(request);
    if (result.platformUserId) setPlatformUserId(result.platformUserId);
    setMessages((items) => [
      ...items,
      { content: result.output, id: assistantId, kind: result.kind }
    ]);
  }

  return (
    <Layout className="chat-shell">
      <header className="chat-header">
        <div>
          <Typography.Title heading={4}>Fluxion 对话</Typography.Title>
          <Typography.Text type="tertiary">{agentDisplayName}</Typography.Text>
        </div>
        <Space>
          <Tag color={platformUserId ? "green" : "grey"}>
            {platformUserId ? `已绑定 ${platformUserId}` : "未绑定"}
          </Tag>
          <Button
            aria-label={mode === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
            icon={mode === "dark" ? <IconSun /> : <IconMoon />}
            onClick={toggle}
            theme="borderless"
          />
        </Space>
      </header>
      <Layout.Content className="chat-content" aria-live="polite">
        {accessError ? <Typography.Text role="alert" type="danger">{accessError}</Typography.Text> : null}
        {messages.length === 0 ? (
          <div className="chat-empty">
            {/* NFR-A11Y-01：Semi Avatar 硬编码 role="listitem"（无列表父级触发
                axe aria-required-parent 且不接受覆盖）——空态直接渲染图标 */}
            <IconUser className="chat-empty-icon" size="extra-large" />
            <Typography.Text type="tertiary">开始对话</Typography.Text>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message) => (
              <article
                className={`message message-${message.kind}`}
                key={message.id}
                aria-label={message.kind === "user" ? "我的消息" : "Fluxion 回复"}
              >
                {message.content}
                {message.kind !== "user" && message.kind !== "error" ? (
                  // TASK-011（S-08）：完成后显示 kind 标签。
                  <span className="message-kind">
                    <Tag size="small">{message.kind}</Tag>
                  </span>
                ) : null}
                {message.kind === "error" ? (
                  <span className="message-error">
                    {message.errorMessage ? (
                      <Typography.Text role="alert" type="danger">
                        {message.errorMessage}
                      </Typography.Text>
                    ) : null}
                    {lastFailedContent !== null ? (
                      <Button
                        aria-label="重试"
                        onClick={() => {
                          // E-04：重试替换失败尝试（移除末尾 error 帧及其配对的用户消息）。
                          setMessages((items) => {
                            const next = [...items];
                            if (next.at(-1)?.kind === "error") next.pop();
                            if (
                              next.at(-1)?.kind === "user" &&
                              next.at(-1)?.content === lastFailedContent
                            ) {
                              next.pop();
                            }
                            return next;
                          });
                          void submit(lastFailedContent);
                        }}
                        size="small"
                      >
                        重试
                      </Button>
                    ) : null}
                  </span>
                ) : null}
              </article>
            ))}
          </div>
        )}
        {sending ? <Spin size="small" aria-label="正在发送" /> : null}
      </Layout.Content>
      <footer className="composer">
        <TextArea
          aria-label="消息"
          autosize={{ minRows: 1, maxRows: 5 }}
          disabled={sending || resolvingAccess || (requiresAccess && !access)}
          onChange={setContent}
          onEnterPress={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              void submit();
            }
          }}
          placeholder="输入消息"
          value={content}
        />
        <Button
          aria-label="发送"
          disabled={!content.trim() || resolvingAccess || (requiresAccess && !access)}
          icon={<IconSend />}
          loading={sending}
          onClick={() => void submit()}
          theme="solid"
          type="primary"
        />
      </footer>
    </Layout>
  );
}
