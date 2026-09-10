import { useEffect, useState } from "react";

import { Input, Modal, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreateMcpServerModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-013：添加 MCP Server Modal——名称/服务地址（仅 streamable_http）。
 * transport/command/args/env/headers 已删除；连接测试/工具发现在独立 MCP
 * Editor 完成。id/version 服务端生成。 */
export function CreateMcpServerModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateMcpServerModalProps) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setName("");
      setUrl("");
      setError(null);
    }
  }, [visible]);

  async function create(): Promise<void> {
    if (!name.trim()) {
      setError("请输入 MCP 名称");
      return;
    }
    if (!url.trim()) {
      setError("必须填写服务地址");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createMcpServer({
        allowed_tools: [],
        display_name: name.trim(),
        name: name.trim(),
        url: url.trim()
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
      title="添加 MCP Server"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>MCP 名称 *</Typography.Text>
          <Input
            aria-label="MCP 名称"
            onChange={(value) => setName(String(value))}
            placeholder="如 weather-mcp"
            value={name}
          />
        </div>
        <div>
          <Typography.Text>服务地址 *</Typography.Text>
          <Input
            aria-label="MCP 服务地址"
            onChange={(value) => setUrl(String(value))}
            placeholder="https://host/mcp"
            value={url}
          />
        </div>
        <Typography.Text type="tertiary" size="small">
          仅支持 Streamable HTTP（远程服务）。创建后进入 MCP Editor 测试连接并发现远端工具。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
