import { useEffect, useState } from "react";

import { Modal, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceVersion } from "../../types/console";

interface CreateSkillModalProps {
  readonly api: ConsoleApi;
  readonly onClose: () => void;
  readonly onCreated: (created: ResourceVersion) => void;
  readonly visible: boolean;
}

/** TASK-012：新建 Skill = Skill Package 上传（ZIP→解析预览→上传发布）。
 * instructions 编辑创建路径已删除（后端 FEAT-08）。 */
export function CreateSkillModal({
  api,
  onClose,
  onCreated,
  visible
}: CreateSkillModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      setFile(null);
      setError(null);
      setPreview(null);
    }
  }, [visible]);

  function onFileChange(event: React.ChangeEvent<HTMLInputElement>): void {
    const selected = event.target.files?.[0] ?? null;
    setError(null);
    setPreview(null);
    if (!selected) {
      setFile(null);
      return;
    }
    if (!selected.name.toLowerCase().endsWith(".zip")) {
      setError("不是有效的 Skill Package（仅支持 .zip）");
      setFile(null);
      return;
    }
    setFile(selected);
    setPreview(`已选择 ${selected.name}（${(selected.size / 1024).toFixed(1)} KB），上传后解析发布`);
  }

  async function upload(): Promise<void> {
    if (!file) {
      setError("请先选择 Skill Package（.zip）");
      return;
    }
    setBusy(true);
    try {
      const created = await api.uploadSkillPackage(file);
      onCreated(created);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      cancelText="取 消"
      okButtonProps={{ loading: busy }}
      okText="上传发布"
      onCancel={onClose}
      onOk={() => void upload()}
      title="新建 Skill"
      visible={visible}
    >
      <div style={{ display: "grid", gap: 12, paddingTop: 8 }}>
        <div>
          <Typography.Text>Skill Package（.zip） *</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <input
              aria-label="Skill Package（.zip）"
              accept=".zip"
              onChange={onFileChange}
              type="file"
            />
          </div>
        </div>
        {preview ? (
          <Typography.Text type="tertiary" size="small">
            解析成功：{preview}
          </Typography.Text>
        ) : null}
        <Typography.Text type="tertiary" size="small">
          Package 须含 manifest.yaml 与 SKILL.md；发布后进入版本治理。
        </Typography.Text>
        {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      </div>
    </Modal>
  );
}
