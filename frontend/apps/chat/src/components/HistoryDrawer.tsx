import React from "react";
import { List, SideSheet, Typography } from "@douyinfe/semi-ui";

import type { WorkspaceHistoryEntry } from "../types/chat";

export interface HistoryDrawerProps {
  readonly open: boolean;
  readonly sessions: readonly WorkspaceHistoryEntry[];
  readonly onClose: () => void;
  readonly onSelect: (sessionId: string) => void;
}

/** 历史抽屉：只读会话列表 + 回看入口，不写不删。 */
export function HistoryDrawer({ open, sessions, onClose, onSelect }: HistoryDrawerProps) {
  return (
    <SideSheet title="历史会话" visible={open} onCancel={onClose} width={320}>
      {sessions.length === 0 ? (
        <Typography.Text type="tertiary">暂无历史会话</Typography.Text>
      ) : (
        <List
          dataSource={[...sessions]}
          renderItem={(entry) => (
            <List.Item
              key={entry.entryId}
              onClick={() => {
                if (entry.conversationId) onSelect(entry.conversationId);
              }}
              style={{ cursor: entry.conversationId ? "pointer" : "default" }}
            >
              <div>
                <Typography.Text strong>{entry.title}</Typography.Text>
                <Typography.Text type="tertiary" style={{ display: "block" }}>
                  {entry.summary}
                </Typography.Text>
              </div>
            </List.Item>
          )}
        />
      )}
    </SideSheet>
  );
}
