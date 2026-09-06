import { useEffect, useState } from "react";

import { Input, Modal, Select, TextArea, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreateToolModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-017（§8.4）：新建 Tool Modal——名称/描述/工具类型（HTTP API / Platform
 * Service）。类型判别进 typed spec（tool_kind）；URL/Method 等配置在独立 Tool
 * Editor 完成。id/version 服务端生成。 */
export function CreateToolModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateToolModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [toolKind, setToolKind] = useState<"http_api" | "platform_service">("http_api");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setName("");
      setDescription("");
      setToolKind("http_api");
      setError(null);
    }
  }, [visible]);

  async function create(): Promise<void> {
    if (!name.trim()) {
      setError("请输入工具名称");
      return;
    }
    setBusy(true);
    try {
      const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const created = await api.createTool({
        name: name.trim(),
        description: description.trim(),
        tool_kind: toolKind,
        // 创建即满足类型分支配置约束（fail-closed）；Editor 中修正为真实配置。
        url: toolKind === "http_api" ? "http://localhost:0/placeholder" : null,
        method: "GET",
        service_name: toolKind === "platform_service" ? slug : null,
        operation: toolKind === "platform_service" ? "invoke" : null,
        capability_ref: `tool:${slug}`,
        adapter_ref: `adapter:${slug}@1`
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
      title="新建 Tool"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>工具名称 *</Typography.Text>
          <Input
            aria-label="工具名称"
            onChange={(value) => setName(String(value))}
            placeholder="如 订单查询"
            value={name}
          />
        </div>
        <div>
          <Typography.Text>工具描述</Typography.Text>
          <TextArea
            aria-label="工具描述"
            onChange={(value) => setDescription(String(value))}
            placeholder="用途说明（可选）"
            value={description}
          />
        </div>
        <div>
          <Typography.Text id="tool-kind-label">工具类型 *</Typography.Text>
          <Select
            aria-labelledby="tool-kind-label"
            onChange={(value) => setToolKind(value === "platform_service" ? "platform_service" : "http_api")}
            optionList={[
              { label: "HTTP API", value: "http_api" },
              { label: "Platform Service", value: "platform_service" }
            ]}
            style={{ width: "100%" }}
            value={toolKind}
          />
        </div>
        <Typography.Text type="tertiary" size="small">
          创建后进入 Tool Editor 配置 {toolKind === "http_api" ? "URL / Method / Headers" : "服务名 / 操作"} 与 Test Call。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
