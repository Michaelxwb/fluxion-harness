import { useEffect, useMemo, useState } from "react";

import { Avatar, Button, Layout, Space, Tag, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconMoon, IconSend, IconSun } from "@douyinfe/semi-icons";

import { Navigate, Route, Routes } from "react-router-dom";

import { WorkspaceLayout } from "./components/WorkspaceLayout";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { InvalidLink } from "./components/InvalidLink";
import { MarkdownMessage } from "./components/MarkdownMessage";
import { TypingIndicator } from "./components/TypingIndicator";
import { ChatPage } from "./pages/ChatPage";
import type {
  ChatAccess,
  ChatApi,
  ChatRequest,
  ChatResultKind,
  WorkspaceHistoryEntry
} from "./types/chat";
import { clearStoredAccessToken } from "./services/httpChatApi";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ChatAppProps {
  readonly api: ChatApi;
  /** TASK-011：从智能体目录携带的 agentId 上下文（无 access 时用于产品名解析）。 */
  readonly initialAgentId?: string;
}

/**
 * Chat 单页路由：`/` 即对话框，其余全部收敛回 `/`（瘦身后唯一页面）。
 * Router 实例（HashRouter/MemoryRouter）由调用方注入。
 */
export function WorkspaceApp({ api }: ChatAppProps) {
  return (
    <Routes>
      <Route element={<WorkspaceLayout api={api} />}>
        <Route path="/" element={<ChatPage api={api} />} />
        {/* 未知路径回对话框，避免空白页 */}
        <Route path="*" element={<Navigate replace to="/" />} />
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
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyEntries, setHistoryEntries] = useState<readonly WorkspaceHistoryEntry[]>([]);

  /** 空态快捷提问（FEAT-07）：点击即发送。 */
  const SUGGESTIONS = useMemo(
    () => ["帮我介绍一下你能做什么", "介绍一下当前智能体", "怎么开始使用"],
    []
  );
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
        // 缓存 token 失效（401/403）则清缓存，避免刷新死循环回绑定页；
        // 其他错误（断网等）保留缓存，下次刷新重试。
        if (
          typeof cause === "object" &&
          cause !== null &&
          "status" in cause &&
          (((cause as { status?: unknown }).status === 401) ||
            ((cause as { status?: unknown }).status === 403))
        ) {
          clearStoredAccessToken();
        }
        if (active) setAccessError(cause instanceof Error ? cause.message : "Chat 访问链接无效");
      })
      .finally(() => {
        if (active) setResolvingAccess(false);
      });
    return () => {
      active = false;
    };
  }, [api, initialAgentId]);

  async function openHistory(): Promise<void> {
    setHistoryOpen(true);
    try {
      setHistoryEntries(await api.listHistory());
    } catch {
      setHistoryEntries([]);
    }
  }

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
          <Button aria-label="历史会话" onClick={() => void openHistory()}>
            历史
          </Button>
          <Button
            aria-label={mode === "dark" ? "切换到亮色模式" : "切换到暗色模式"}
            icon={mode === "dark" ? <IconSun /> : <IconMoon />}
            onClick={toggle}
            theme="borderless"
          />
        </Space>
      </header>
      <Layout.Content className="chat-content" aria-live="polite">
        {accessError ? (
          <InvalidLink />
        ) : null}
        {accessError ? null : messages.length === 0 ? (
          <div className="chat-empty">
            <Typography.Title heading={4}>你好，我是{agentDisplayName}</Typography.Title>
            <Typography.Text type="tertiary">有什么可以帮你的吗？</Typography.Text>
            <Space wrap style={{ marginTop: 12 }}>
              {SUGGESTIONS.map((text) => (
                <Button key={text} theme="borderless" onClick={() => void submit(text)}>
                  {text}
                </Button>
              ))}
            </Space>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message, index) => (
              <article
                className={`message ${
                  message.kind === "user"
                    ? "message-user"
                    : message.kind === "error"
                      ? "message-error"
                      : "message-flat"
                }`}
                key={message.id}
                aria-label={message.kind === "user" ? "我的消息" : "Fluxion 回复"}
              >
                {message.kind === "user" ? (
                  message.content
                ) : message.kind === "error" ? (
                  // E-04：中断保留已收内容（纯文本，避免半截 Markdown 误渲染）。
                  message.content || null
                ) : (
                  <div className="message-assistant-head">
                    <Avatar size="extra-small" className="message-avatar">
                      {agentDisplayName.slice(0, 1)}
                    </Avatar>
                    <Typography.Text strong className="message-name">
                      {agentDisplayName}
                    </Typography.Text>
                  </div>
                )}
                {message.kind === "user" || message.kind === "error" ? null : sending &&
                  index === messages.length - 1 &&
                  message.content.length === 0 ? (
                  // 首 token 未到：打字机指示器（替代光标 + 底部转圈）。
                  <TypingIndicator />
                ) : (
                  <MarkdownMessage
                    content={
                      sending && index === messages.length - 1
                        ? `${message.content}▍`
                        : message.content
                    }
                  />
                )}
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
        {/* 发送中状态由消息区 TypingIndicator 承载，此处不再重复转圈 */}
      </Layout.Content>
      <HistoryDrawer
        open={historyOpen}
        sessions={historyEntries}
        onClose={() => setHistoryOpen(false)}
        onSelect={() => setHistoryOpen(false)}
      />
      <footer className="composer">
        <TextArea
          aria-label="消息"
          autosize={{ minRows: 2, maxRows: 6 }}
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
