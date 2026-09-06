import { useEffect, useState } from "react";

import { Input, Modal, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreatePolicyModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-022（§8.11）：新建授权规则 Modal——名称；创建后进入独立 Policy Editor
 * 结构化编辑 allowed/denied tools（typed 表单，非 raw JSON / SchemaForm）。 */
export function CreatePolicyModal({
  api,
  onClose,
  onCreated,
  visible
}: CreatePolicyModalProps) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setName("");
      setError(null);
    }
  }, [visible]);

  async function create(): Promise<void> {
    if (!name.trim()) {
      setError("请输入策略名称");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createPolicy({
        name: name.trim(),
        allowed_tools: [],
        denied_tools: []
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
      title="新建规则"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>策略名称 *</Typography.Text>
          <Input
            aria-label="策略名称"
            onChange={(value) => setName(String(value))}
            placeholder="如 tenant-tool-baseline"
            value={name}
          />
        </div>
        <Typography.Text type="tertiary" size="small">
          创建后进入 Policy Editor 配置工具白/黑名单；发布后经租户 Binding 生效。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
