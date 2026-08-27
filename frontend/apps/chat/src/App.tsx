import { useEffect, useMemo, useState } from "react";

import { Avatar, Button, Layout, Space, Spin, Tag, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconMoon, IconSend, IconSun, IconUser } from "@douyinfe/semi-icons";

import type { ChatAccess, ChatApi, ChatRequest, ChatResultKind } from "./types/chat";
import { useThemeMode } from "./theme";
import "./styles.css";

interface ChatAppProps {
  readonly api: ChatApi;
}

interface ChatItem {
  readonly content: string;
  readonly id: string;
  readonly kind: ChatResultKind | "user" | "error";
}

export function ChatApp({ api }: ChatAppProps) {
  const { mode, toggle } = useThemeMode();
  const [content, setContent] = useState("");
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [platformUserId, setPlatformUserId] = useState<string>();
  const [access, setAccess] = useState<ChatAccess | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [resolvingAccess, setResolvingAccess] = useState(api.resolveAccess !== undefined);
  const [sending, setSending] = useState(false);
  const conversationId = useMemo(() => `conversation-${Date.now()}`, []);
  const requiresAccess = api.resolveAccess !== undefined;

  useEffect(() => {
    if (!api.resolveAccess) return;
    let active = true;
    void api.resolveAccess()
      .then((resolved) => {
        if (!active) return;
        setAccess(resolved);
        setPlatformUserId(resolved.platformUserId);
        setAccessError(null);
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
  }, [api]);

  async function submit(): Promise<void> {
    const trimmed = content.trim();
    if (!trimmed || sending || (requiresAccess && !access)) return;
    const messageId = `message-${Date.now()}-${messages.length}`;
    const assistantId = `${messageId}-response`;
    setContent("");
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
      updateAssistant(() => ({
        content: error instanceof Error ? error.message : "消息发送失败",
        id: assistantId,
        kind: "error"
      }));
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
        updateAssistant((item) => ({
          ...item,
          content: event.message ?? "消息发送失败",
          kind: "error"
        }));
      }
    });
    if (!receivedOutput) {
      updateAssistant((item) => ({ ...item, content: "（无输出）", kind: "error" }));
    }
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
          <Typography.Text type="tertiary">{access?.agentId ?? "智能体"}</Typography.Text>
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
            <Avatar color="light-blue" size="large">
              <IconUser />
            </Avatar>
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
