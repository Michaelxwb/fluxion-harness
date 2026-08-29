/**
 * X405 审批（TASK-008 / FEAT-P4-05）：HumanTask 审批队列容器。
 * `{ pending: Map<id, submitting> }` 防重复提交；接口失败 → 错误提示 + 列表保持待确认（E-03）。
 */
import { useEffect, useState } from "react";

import { Empty, Skeleton, Typography } from "@douyinfe/semi-ui";

import { ApprovalList } from "../components/ApprovalList";
import { ErrorBanner } from "../components/ErrorBanner";
import type { ApprovalDecision, ChatApi, WorkspaceApproval } from "../types/chat";

interface ApprovalsPageProps {
  readonly api: ChatApi;
}

export function ApprovalsPage({ api }: ApprovalsPageProps) {
  const [items, setItems] = useState<readonly WorkspaceApproval[] | null>(null);
  const [pending, setPending] = useState<Map<string, "submitting">>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    void api
      .listApprovals()
      .then((list) => {
        if (active) setItems(list);
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "未知错误");
      });
    return () => {
      active = false;
    };
  }, [api, reloadKey]);

  async function decide(
    approvalId: string,
    decision: ApprovalDecision,
    comment?: string
  ): Promise<void> {
    if (pending.has(approvalId)) return;
    setPending((map) => new Map(map).set(approvalId, "submitting"));
    setFeedback(null);
    setError(null);
    try {
      await api.decideApproval(approvalId, decision, comment);
      setFeedback(decision === "approve" ? "已通过" : "已拒绝");
      setItems(await api.listApprovals());
    } catch (cause) {
      setError(`操作失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    } finally {
      setPending((map) => {
        const next = new Map(map);
        next.delete(approvalId);
        return next;
      });
    }
  }

  return (
    <section aria-label="审批" className="approvals-page">
      <Typography.Title heading={3}>审批</Typography.Title>
      {feedback !== null ? (
        <Typography.Text role="status" type="success">
          {feedback}
        </Typography.Text>
      ) : null}
      {error !== null ? (
        <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />
      ) : null}
      {items === null ? (
        <div aria-label="审批列表加载中">
          <Skeleton.Title />
        </div>
      ) : items.length === 0 ? (
        <Empty description="没有待确认事项" />
      ) : (
        <ApprovalList items={items} pending={pending} onDecide={(id, d, c) => void decide(id, d, c)} />
      )}
    </section>
  );
}
