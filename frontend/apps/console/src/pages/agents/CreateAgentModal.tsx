import { useEffect, useState } from "react";

import { Button, Input, Modal, Select, Space, TextArea, Toast, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceSummary } from "../../types/console";

interface CreateAgentModalProps {
  readonly api: ConsoleApi;
  readonly visible: boolean;
  readonly onClose: () => void;
  readonly onCreated: (resourceId: string) => void;
}

/** TASK-012（返工）：CreateAgentModal——最小建档（名称/描述/默认模型）。
 *
 * Create ≠ Configure：ID/Version 系统生成；无 ResourceKind 下拉、无 resource_id/
 * version/timeout/raw JSON。创建后进入专属 Editor 完整配置（TASK-014）。
 * ADR-A008：默认模型必须是已存在的 ModelDefinition（Select 选择，非自由文本），
 * 生成 model_policy.primary_model_ref；system_prompt 非空（后端 min_length=1），
 * 最小建档用默认提示词，进入 Editor 后完善。
 */
export function CreateAgentModal({ api, visible, onClose, onCreated }: CreateAgentModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<ResourceSummary | null>(null);
  const [models, setModels] = useState<readonly ResourceSummary[] | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void api.listResources("model_definition").then(
      (page) => {
        if (active) setModels(page.items);
      },
      () => {
        if (active) setModels([]);
      }
    );
    return () => {
      active = false;
    };
  }, [api, visible]);

  async function submit(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("智能体名称：必填");
      return;
    }
    if (!model) {
      setError("默认模型：必选（选择一个 ModelDefinition）");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const resourceId = `agent-${slugify(trimmed)}-${Date.now().toString(36)}`;
      await api.createResource({
        resourceType: "agent_definition",
        resourceId,
        version: "1",
        visibility: "private",
        spec: {
          name: trimmed,
          description: description.trim(),
          system_prompt: `你是${trimmed}，请严谨、专业地完成任务。`,
          owner: "default",
          model_policy: {
            primary_model_ref: { id: model.resourceId, version: model.currentVersion },
            fallback_model_refs: []
          },
          capabilities: []
        }
      });
      setName("");
      setDescription("");
      setModel(null);
      // 先关闭 + 刷新列表（可观测结果），Toast 独立 try/catch 不阻断建档流程
      onCreated(resourceId);
      try {
        Toast.success("智能体已创建");
      } catch {
        // jsdom/无 Toast 容器下 Toast 渲染失败不影响建档
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            aria-label="创建智能体"
            loading={submitting}
            onClick={() => void submit()}
            theme="solid"
            type="primary"
          >
            创建
          </Button>
        </Space>
      }
      motion={false}
      onCancel={onClose}
      title="新建智能体"
      visible={visible}
    >
      <div style={{ display: "grid", rowGap: 16 }}>
        <div>
          <Typography.Text>名称 *</Typography.Text>
          <Input
            aria-label="智能体名称"
            onChange={(value) => {
              setName(String(value));
              setError(null);
            }}
            placeholder="如：客户服务助手"
            value={name}
          />
        </div>
        <div>
          <Typography.Text>描述</Typography.Text>
          <TextArea
            aria-label="智能体描述"
            onChange={(value) => setDescription(String(value))}
            placeholder="用途说明"
            value={description}
          />
        </div>
        <div>
          <Typography.Text>默认模型 *</Typography.Text>
          <Select
            aria-label="默认模型"
            onChange={(value) => {
              const id = String(value ?? "");
              setModel(models?.find((item) => item.resourceId === id) ?? null);
              setError(null);
            }}
            optionList={(models ?? []).map((item) => ({
              value: item.resourceId,
              label: `${item.displayName}（${item.resourceId}）`
            }))}
            placeholder={models === null ? "加载模型…" : "选择模型"}
            value={model?.resourceId}
          />
          {models !== null && models.length === 0 ? (
            <Typography.Text type="tertiary">
              暂无可用模型，请先在「平台 → 模型」创建 ModelDefinition
            </Typography.Text>
          ) : null}
        </div>
        {error ? (
          <Typography.Text type="danger" role="alert">
            {error}
          </Typography.Text>
        ) : null}
      </div>
    </Modal>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
