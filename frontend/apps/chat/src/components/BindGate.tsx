/**
 * 未绑定用户的 /bind 引导流程（TASK-003 / B-01，正式 Channel 规则：
 * 未绑定仅 /bind 可见）。经真实 `sendMessage("/bind <code>")` 状态机完成绑定。
 */
import { useState } from "react";

import { Avatar, Button, Card, Input, Typography } from "@douyinfe/semi-ui";
import { IconKey } from "@douyinfe/semi-icons";

import type { ChatApi } from "../types/chat";

interface BindGateProps {
  readonly api: ChatApi;
  readonly onBound: (platformUserId: string) => void;
}

export function BindGate({ api, onBound }: BindGateProps) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    const trimmed = code.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.sendMessage({
        content: `/bind ${trimmed}`,
        conversationId: `bind-${Date.now()}`,
        messageId: `bind-${Date.now()}`
      });
      if (response.kind === "bound" && response.platformUserId) {
        onBound(response.platformUserId);
      } else {
        setError(response.output || "绑定失败，请检查绑定码");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "绑定失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bind-gate">
      <Card className="bind-card">
        <div className="bind-header">
          <Avatar color="light-blue" size="large">
            <IconKey />
          </Avatar>
          <Typography.Title heading={4}>绑定账号</Typography.Title>
          <Typography.Text type="tertiary">
            输入渠道绑定码，将本会话绑定到平台账号
          </Typography.Text>
        </div>
        <div className="bind-form">
          <Input
            aria-label="绑定码"
            className="bind-input"
            onChange={setCode}
            placeholder="绑定码"
            value={code}
          />
          <Button
            aria-label="绑定"
            disabled={!code.trim()}
            htmlType="submit"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="primary"
          >
            绑定
          </Button>
        </div>
        {error ? (
          <Typography.Text role="alert" type="danger">
            {error}
          </Typography.Text>
        ) : null}
      </Card>
    </div>
  );
}
