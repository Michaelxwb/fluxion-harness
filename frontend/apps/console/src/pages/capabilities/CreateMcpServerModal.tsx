import { useEffect, useState } from "react";

import { Input, Modal, Select, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreateMcpServerModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-018（§8.5）：添加 MCP Server Modal——名称/Transport/URL。
 * transport+url 满足 MCPDefinition validator（fail-closed）；连接测试/工具发现
 * 在独立 MCP Editor 完成。id/version 服务端生成。 */
export function CreateMcpServerModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateMcpServerModalProps) {
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"streamable_http" | "stdio">("streamable_http");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setName("");
      setTransport("streamable_http");
      setUrl("");
      setError(null);
    }
  }, [visible]);

  async function create(): Promise<void> {
    if (!name.trim()) {
      setError("请输入 MCP 名称");
      return;
    }
    if (transport === "streamable_http" && !url.trim()) {
      setError("streamable_http 类型必须填写服务地址");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createMcpServer({
        name: name.trim(),
        display_name: name.trim(),
        transport,
        url: transport === "streamable_http" ? url.trim() : null,
        command: transport === "stdio" ? "npx" : null,
        args: [],
        allowed_tools: []
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
          <Typography.Text id="mcp-transport-label">连接方式 *</Typography.Text>
          <Select
            aria-labelledby="mcp-transport-label"
            onChange={(value) => setTransport(value === "stdio" ? "stdio" : "streamable_http")}
            optionList={[
              { label: "Streamable HTTP（远程服务）", value: "streamable_http" },
              { label: "stdio（本地进程）", value: "stdio" }
            ]}
            style={{ width: "100%" }}
            value={transport}
          />
        </div>
        {transport === "streamable_http" ? (
          <div>
            <Typography.Text>服务地址 *</Typography.Text>
            <Input
              aria-label="MCP 服务地址"
              onChange={(value) => setUrl(String(value))}
              placeholder="https://host/mcp"
              value={url}
            />
          </div>
        ) : null}
        <Typography.Text type="tertiary" size="small">
          创建后进入 MCP Editor 测试连接并发现远端工具。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
