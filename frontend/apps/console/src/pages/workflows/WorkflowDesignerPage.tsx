import { useEffect, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";
import { Button, Card, Descriptions, Empty, Modal, Space, Spin, Tabs, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import { JsonEditorTab } from "../../components/studio/JsonEditorTab";
import { NodeConfigForm } from "../../components/studio/NodeConfigForm";
import { WorkflowNodeList } from "../../components/studio/WorkflowNodeList";
import type {
  ConsoleApi,
  JsonRecord,
  ResourceVersion,
  WorkflowDraftV2,
  WorkflowV2Node
} from "../../types/console";

interface WorkflowDesignerPageProps {
  readonly api: ConsoleApi;
}

interface PublishIssue {
  readonly nodeId: string | null;
  readonly text: string;
}

/** TASK-015（§8.2）：独立 Workflow Designer 路由（`/build/workflows/:id/edit`）。
 *
 * - FEAT-F06 无感化：published 资源打开即自动 working draft（对齐 Agent/Model
 *   Editor），无显式「创建草稿/校验」按钮——校验在保存/发布底层自动执行。
 * - 操作收敛 [保存][发布]；发布前自动 V2 判别联合校验 + 完整发布校验。
 */
export function WorkflowDesignerPage({ api }: WorkflowDesignerPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState<WorkflowDraftV2 | null>(null);
  const [specText, setSpecText] = useState("{}");
  const [selectedNodeIndex, setSelectedNodeIndex] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishIssues, setPublishIssues] = useState<readonly PublishIssue[] | null>(null);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  // ADR-A011：乐观并发 base（fork 前的 published 版本）
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await api.getResource("workflow", resourceId);
        const target =
          loaded.status === "published"
            ? await api.createDraftFromLatest("workflow", resourceId)
            : loaded;
        if (!active) return;
        setResource(target);
        setPublishedBaseVersion(loaded.status === "published" ? loaded.version : undefined);
        const nextDraft = parseDraft(target.spec);
        setDraft(nextDraft);
        setSpecText(JSON.stringify(target.spec, null, 2));
        setSelectedNodeIndex(nextDraft.steps.length > 0 ? 0 : null);
        if (target.spec && typeof target.spec.engine_ref === "string") {
          setNotice("该 spec 含已弃用的 engine_ref（V2 不进入 Product DSL），保存后将被移除");
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId]);

  function updateDraft(next: WorkflowDraftV2): void {
    setDraft(next);
    setSpecText(JSON.stringify(draftToSpec(next), null, 2));
  }

  function updateNode(next: WorkflowV2Node): void {
    if (!draft || selectedNodeIndex === null) return;
    updateDraft({
      ...draft,
      steps: draft.steps.map((node, index) => (index === selectedNodeIndex ? next : node))
    });
  }

  function addNode(): void {
    if (!draft) return;
    const id = uniqueNodeId(draft);
    updateDraft({
      ...draft,
      steps: [
        ...draft.steps,
        { capability_ref: "", depends_on: [], id, input: {}, type: "capability" }
      ]
    });
    setSelectedNodeIndex(draft.steps.length);
  }
  function removeNode(index: number): void {
    if (!draft) return;
    updateDraft({ ...draft, steps: draft.steps.filter((_, i) => i !== index) });
    setSelectedNodeIndex((current) => {
      if (current === null) return null;
      if (current === index) return null;
      return current > index ? current - 1 : current;
    });
  }

  /** 诊断定位：按节点 ID 选中对应步骤（找不到则保持现状）。 */
  function selectNodeById(nodeId: string): void {
    if (!draft) return;
    const index = draft.steps.findIndex((node) => node.id === nodeId);
    if (index >= 0) setSelectedNodeIndex(index);
  }

  async function save(): Promise<void> {
    if (!resource || !draft) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const updated = await api.updateDraft(target, draftToSpec(draft));
      setResource(updated);
      setNotice("已保存");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  /** S-06：已发布版本不可变——写前若工作态已是 published，先 fork 新 Draft
   * 再写，否则直接 update 会 409。draft 态零额外请求直接返回。
   * 注意只切换 resource，不碰 draft/specText（表单持有最新未存内容）。
   */
  async function ensureDraft(): Promise<ResourceVersion> {
    if (!resource) throw new Error("资源尚未加载");
    if (resource.status !== "published") return resource;
    const next = await api.createDraftFromLatest("workflow", resource.resourceId);
    setResource(next);
    return next;
  }

  /** 发布点击 = 校验底层自动（V2 判别联合 + 完整发布校验），通过才弹确认。 */
  async function requestPublish(): Promise<void> {
    if (!resource || !draft) return;
    setBusy(true);
    setError(null);
    try {
      const v2 = await api.validateWorkflow(draft);
      if (!v2.valid) {
        setPublishIssues(
          v2.diagnostics.map((item) => ({
            nodeId: item.nodeId ?? null,
            text: `${item.nodeId ? `节点 ${item.nodeId} ` : ""}${item.field}: ${item.message}`
          }))
        );
        setNotice("无法发布");
        return;
      }
      const target = await ensureDraft();
      const updated = await api.updateDraft(target, draftToSpec(draft));
      const validation = await api.validatePublish(updated);
      if (!validation.valid) {
        setResource(updated);
        setPublishIssues(validation.diagnostics.map((text) => ({ nodeId: null, text })));
        setNotice("无法发布");
        return;
      }
      setResource(updated);
      setPublishIssues(null);
      setConfirmVisible(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发布校验失败");
    } finally {
      setBusy(false);
    }
  }

  async function publish(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.publishVersion(resource, { expectedBaseVersion: publishedBaseVersion });
      // S-06：发布成功即为 published 态；后续保存经 ensureDraft fork 新版。
      setResource({ ...resource, status: "published" });
      setPublishedBaseVersion(result.version);
      setConfirmVisible(false);
      setNotice(`已发布 ${result.version}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发布失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="page-stack">
        <Typography.Title heading={3}>工作流 Designer</Typography.Title>
        <Card>
          <div aria-label="Designer 加载中">
            <Spin />
          </div>
        </Card>
      </div>
    );
  }

  const selectedNode =
    selectedNodeIndex === null ? null : draft?.steps[selectedNodeIndex] ?? null;

  return (
    <div className="page-stack">
      <Space align="center">
        <Typography.Title heading={3}>工作流 Designer</Typography.Title>
        {resource ? (
          <Button onClick={() => navigate("/build/workflows")} type="tertiary">
            返回列表
          </Button>
        ) : null}
      </Space>
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {resource ? (
        <Card aria-label="Workflow Designer" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Descriptions row>
            <Descriptions.Item itemKey="工作流">{resource.resourceId}</Descriptions.Item>
            <Descriptions.Item itemKey="版本">{resource.version}</Descriptions.Item>
            <Descriptions.Item itemKey="状态"><StatusTag status={resource.status} /></Descriptions.Item>
          </Descriptions>
          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid" type="primary">
              保存
            </Button>
            <Button loading={busy} onClick={() => void requestPublish()} type="primary">
              发布
            </Button>
          </Space>
          {publishIssues?.length ? (
            <div aria-label="校验诊断">
              <Typography.Text type="danger">无法发布，发现 {publishIssues.length} 个问题：</Typography.Text>
              <ul className="studio-diagnostics">
                {publishIssues.map((issue) => (
                  <li key={issue.text}>
                    {issue.nodeId ? (
                      <Button
                        onClick={() => selectNodeById(issue.nodeId as string)}
                        theme="borderless"
                        type="danger"
                      >
                        {issue.text}（点击定位）
                      </Button>
                    ) : (
                      <Typography.Text type="danger">{issue.text}</Typography.Text>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <Tabs defaultActiveKey="form" type="line">
            <Tabs.TabPane itemKey="form" tab="表单模式">
              <div className="studio-form-mode">
                <WorkflowNodeList
                  nodes={draft?.steps ?? []}
                  onAdd={addNode}
                  onRemove={removeNode}
                  onSelect={setSelectedNodeIndex}
                  selectedIndex={selectedNodeIndex}
                />
                {selectedNode ? (
                  <NodeConfigForm node={selectedNode} onChange={updateNode} />
                ) : (
                  <Empty description="选择左侧节点进行配置" />
                )}
              </div>
            </Tabs.TabPane>
            <Tabs.TabPane itemKey="json" tab="JSON 高级模式">
              <JsonEditorTab
                onChange={(value) => {
                  setSpecText(value);
                  try {
                    setDraft(parseDraftText(value));
                  } catch {
                    // JSON 编辑中间态：语法未完整时保留上一份草稿
                  }
                }}
                specText={specText}
              />
            </Tabs.TabPane>
          </Tabs>
        </Card>
      ) : null}
      <Modal
        footer={
          <Space>
            <Button onClick={() => setConfirmVisible(false)}>取消</Button>
            <Button loading={busy} onClick={() => void publish()} theme="solid" type="primary">
              确认发布
            </Button>
          </Space>
        }
        onCancel={() => setConfirmVisible(false)}
        title="确认发布工作流"
        visible={confirmVisible}
      >
        {resource ? (
          <Space align="start" vertical>
            <Typography.Text>{`workflow/${resource.resourceId}`}</Typography.Text>
            <Typography.Text>{resource.version}</Typography.Text>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// draft ↔ spec 转换（与旧 WorkflowsPage 相同语义，随 Designer 迁移）

function parseDraft(spec: JsonRecord): WorkflowDraftV2 {
  return parseDraftText(JSON.stringify(spec));
}

function parseDraftText(value: string): WorkflowDraftV2 {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Workflow DSL 必须是 JSON Object");
  }
  const record = parsed as Record<string, unknown>;
  const steps = Array.isArray(record.steps) ? record.steps : [];
  return {
    description: typeof record.description === "string" ? record.description : undefined,
    display_name: typeof record.display_name === "string" ? record.display_name : undefined,
    name: typeof record.name === "string" ? record.name : "",
    steps: steps.map(normalizeNode)
  };
}

function normalizeNode(step: unknown): WorkflowV2Node {
  const node = (typeof step === "object" && step !== null ? step : {}) as Record<string, unknown>;
  const type = typeof node.type === "string" ? node.type : "capability";
  return { ...node, type } as unknown as WorkflowV2Node;
}

function draftToSpec(draft: WorkflowDraftV2): JsonRecord {
  const spec: Record<string, unknown> = {
    name: draft.name,
    steps: draft.steps
  };
  if (draft.display_name !== undefined) spec.display_name = draft.display_name;
  if (draft.description !== undefined) spec.description = draft.description;
  return spec as JsonRecord;
}

function uniqueNodeId(draft: WorkflowDraftV2): string {
  const existing = new Set(draft.steps.map((node) => node.id));
  let index = draft.steps.length + 1;
  while (existing.has(`node-${index}`)) index += 1;
  return `node-${index}`;
}
