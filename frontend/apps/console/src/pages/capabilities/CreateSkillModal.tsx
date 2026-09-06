import { useEffect, useState } from "react";

import { Input, Modal, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreateSkillModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-016（§8.3）：新建 Skill Modal——名称/描述；创建即进入独立 Skill Editor
 * 编辑复杂内容（instructions/required_capabilities）。id/version 服务端生成。 */
export function CreateSkillModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateSkillModalProps) {
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
      setError("请输入技能名称");
      return;
    }
    setBusy(true);
    try {
      const created = await api.createSkill({
        name: name.trim(),
        instructions: "",
        required_capabilities: [],
        visibility: "public"
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
      title="新建 Skill"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>技能名称 *</Typography.Text>
          <Input
            aria-label="技能名称"
            onChange={(value) => setName(String(value))}
            placeholder="如 周报汇总"
            value={name}
          />
        </div>
        <Typography.Text type="tertiary" size="small">
          创建后进入 Skill Editor 编辑做法说明与所需能力。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
