import { useEffect, useMemo, useState } from "react";

import { Avatar, Button, Layout, Spin, Tag, TextArea, Typography } from "@douyinfe/semi-ui";
import { IconSend, IconUser } from "@douyinfe/semi-icons";

import type { ChatAccess, ChatApi, ChatResultKind } from "./types/chat";
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
    setContent("");
    setMessages((items) => [...items, { content: trimmed, id: messageId, kind: "user" }]);
    setSending(true);
    try {
      const result = await api.sendMessage({
        content: trimmed,
        conversationId,
        messageId
      });
      if (result.platformUserId) setPlatformUserId(result.platformUserId);
      setMessages((items) => [
        ...items,
        { content: result.output, id: `${messageId}-response`, kind: result.kind }
      ]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "消息发送失败";
      setMessages((items) => [...items, { content: message, id: `${messageId}-error`, kind: "error" }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <Layout className="chat-shell">
      <header className="chat-header">
        <div>
          <Typography.Title heading={4}>Fluxion Chat</Typography.Title>
          <Typography.Text type="tertiary">{access?.runtimeProfileId ?? "Agent"}</Typography.Text>
        </div>
        <Tag color={platformUserId ? "green" : "grey"}>
          {platformUserId ? `已绑定 ${platformUserId}` : "未绑定"}
        </Tag>
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
