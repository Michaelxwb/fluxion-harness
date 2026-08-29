/** 展示组件：对话 + 任务统一时间线（倒序；行内展开详情，trace 关联入口）。 */
import { useState } from "react";

import { Button, Empty, Tag, Typography } from "@douyinfe/semi-ui";

import type { WorkspaceHistoryEntry } from "../types/chat";

type HistoryTagColor = "blue" | "cyan";

const KIND_META: Readonly<Record<WorkspaceHistoryEntry["kind"], { color: HistoryTagColor; text: string }>> = {
  chat: { color: "blue", text: "对话" },
  task: { color: "cyan", text: "任务" }
};

interface HistoryTimelineProps {
  readonly entries: readonly WorkspaceHistoryEntry[];
}

export function HistoryTimeline({ entries }: HistoryTimelineProps) {
  return (
    <section className="history-timeline">
      {entries.length === 0 ? (
        <Empty description="暂无历史记录" />
      ) : (
        <ul aria-label="历史时间线" className="history-entries">
          {entries.map((entry) => (
            <HistoryRow entry={entry} key={entry.entryId} />
          ))}
        </ul>
      )}
    </section>
  );
}

function HistoryRow({ entry }: { readonly entry: WorkspaceHistoryEntry }) {
  const [expanded, setExpanded] = useState(false);
  const kind = KIND_META[entry.kind];

  return (
    <li className="history-row">
      <Button
        aria-expanded={expanded}
        aria-label={`${entry.title}（${kind.text}）`}
        className="history-row-button"
        onClick={() => setExpanded((value) => !value)}
        theme="light"
      >
        <span className="history-title">{entry.title}</span>
        <Tag color={kind.color}>{kind.text}</Tag>
        <span className="history-at">{entry.at}</span>
      </Button>
      {expanded ? (
        <div aria-label="历史详情" className="history-detail">
          <Typography.Text type="tertiary">{entry.summary}</Typography.Text>
          {entry.traceId ? (
            <Typography.Text type="secondary" size="small">
              关联 trace：{entry.traceId}
            </Typography.Text>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
