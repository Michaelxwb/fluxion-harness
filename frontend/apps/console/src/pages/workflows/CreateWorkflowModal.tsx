import { useEffect, useState } from "react";

import { Input, Modal, Select, TextArea, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceSummary, ResourceVersion } from "../../types/console";

interface CreateWorkflowModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/**
 * TASK-015（§8.2）：新建工作流 Modal——名称/描述 + 首个步骤能力。
 *
 * 首个步骤能力为 DSL 契约要求（WorkflowDefinition.steps min 1），非 UI 决策：
 * 创建即产生结构完整的 draft，后续编排全部在独立 Designer 完成。
 * id/version 由服务端生成（产品端点，前端不生成）。
 */
export function CreateWorkflowModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateWorkflowModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [capabilities, setCapabilities] = useState<readonly ResourceSummary[]>([]);
  const [capabilityRef, setCapabilityRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void (async () => {
      try {
        const [skills, tools, mcps] = await Promise.all([
          api.listVisibleResources("skill"),
          api.listVisibleResources("tool"),
          api.listVisibleResources("mcp")
        ]);
        if (!active) return;
        setCapabilities([...skills, ...tools, ...mcps]);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "能力列表加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, visible]);

  useEffect(() => {
    if (visible) {
      setName("");
      setDescription("");
      setCapabilityRef("");
      setError(null);
    }
  }, [visible]);

  async function create(): Promise<void> {
    if (!name.trim()) {
      setError("请输入工作流名称");
      return;
    }
    if (!capabilityRef) {
      setError("请选择首个步骤能力");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createWorkflow({
        name: name.trim(),
        description: description.trim(),
        steps: [
          {
            id: "node-1",
            type: "capability",
            capability_ref: capabilityRef,
            depends_on: [],
            input: {}
          }
        ]
      });
      onCreated(created);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      cancelText="取 消"
      okText="创 建"
      okButtonProps={{ loading: busy }}
      onOk={() => void create()}
      onCancel={onClose}
      title="新建工作流"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>工作流名称 *</Typography.Text>
          <Input
            aria-label="工作流名称"
            onChange={(value) => setName(String(value))}
            placeholder="如 订单审核流"
            value={name}
          />
        </div>
        <div>
          <Typography.Text>工作流描述</Typography.Text>
          <TextArea
            aria-label="工作流描述"
            onChange={(value) => setDescription(String(value))}
            placeholder="用途说明（可选）"
            value={description}
          />
        </div>
        <div>
          <Typography.Text id="workflow-first-capability-label">首个步骤能力 *</Typography.Text>
          <Select
            aria-labelledby="workflow-first-capability-label"
            filter
            onChange={(value) => setCapabilityRef(String(value ?? ""))}
            optionList={capabilities.map((item) => ({
              label: `${item.displayName || item.resourceId}（${item.currentVersion}）`,
              value: `${item.resourceType}:${item.resourceId}@${item.currentVersion}`
            }))}
            placeholder="选择已发布的能力（skill/tool/mcp）"
            style={{ width: "100%" }}
            value={capabilityRef}
          />
          <Typography.Text type="tertiary" size="small">
            DSL 要求工作流至少 1 个节点；后续步骤在 Designer 中编排。
          </Typography.Text>
        </div>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
