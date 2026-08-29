/**
 * 展示组件：HumanTask 审批行（通过/拒绝/留言；props 只读，决策事件上抛）。
 * 操作进行中（submitting）按钮禁用，防重复提交。
 */
import { useState } from "react";

import { Button, Tag, TextArea } from "@douyinfe/semi-ui";

import type { ApprovalDecision, WorkspaceApproval } from "../types/chat";

interface ApprovalRowProps {
  readonly approval: WorkspaceApproval;
  readonly submitting: boolean;
  readonly onDecide: (
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string
  ) => void;
}

export function ApprovalRow({ approval, submitting, onDecide }: ApprovalRowProps) {
  const [comment, setComment] = useState("");

  return (
    <li className="approval-row">
      <div className="approval-head">
        <span className="approval-title">{approval.title}</span>
        <Tag color="orange">待确认</Tag>
      </div>
      <p className="approval-message">{approval.message}</p>
      <TextArea
        aria-label="留言"
        disabled={submitting}
        onChange={setComment}
        placeholder="留言（可选）"
        value={comment}
      />
      <div className="approval-actions">
        <Button
          disabled={submitting}
          loading={submitting}
          onClick={() => onDecide(approval.approvalId, "approve", comment || undefined)}
          theme="solid"
          type="primary"
        >
          通过
        </Button>
        <Button
          disabled={submitting}
          onClick={() => onDecide(approval.approvalId, "reject", comment || undefined)}
          type="danger"
        >
          拒绝
        </Button>
      </div>
    </li>
  );
}

interface ApprovalListProps {
  readonly items: readonly WorkspaceApproval[];
  readonly pending: ReadonlyMap<string, "submitting">;
  readonly onDecide: (
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string
  ) => void;
}

export function ApprovalList({ items, pending, onDecide }: ApprovalListProps) {
  return (
    <section aria-label="待确认事项" className="approval-list">
      <ul>
        {items.map((approval) => (
          <ApprovalRow
            approval={approval}
            key={approval.approvalId}
            onDecide={onDecide}
            submitting={pending.has(approval.approvalId)}
          />
        ))}
      </ul>
    </section>
  );
}
