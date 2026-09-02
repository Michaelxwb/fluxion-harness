import { useEffect, useState } from "react";

import { Button, Modal, Space, Spin, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import type { ConsoleApi, JsonRecord, ResourceVersion } from "../../types/console";
import { ModelFields, ProviderFields } from "./ModelResourceFields";

type EditableModelKind = "model_provider" | "model_definition";

interface ModelResourceEditorProps {
  readonly api: ConsoleApi;
  readonly kind: EditableModelKind;
  readonly providerOptions: readonly { readonly label: string; readonly value: string }[];
  readonly resourceId: string;
  readonly onClose: () => void;
  readonly onSaved: () => void;
}

/** Provider/Model 专用编辑器：typed 控件 + working draft + 发布校验。 */
export function ModelResourceEditor({
  api,
  kind,
  providerOptions,
  resourceId,
  onClose,
  onSaved
}: ModelResourceEditorProps) {
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [spec, setSpec] = useState<JsonRecord>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<readonly string[]>([]);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const loaded = await api.getResource(kind, resourceId);
        const draft = loaded.status === "published"
          ? await api.createDraftFromLatest(kind, resourceId)
          : loaded;
        if (active) {
          setResource(draft);
          setSpec({ ...draft.spec });
        }
      } catch (cause) {
        if (active) setError(messageFrom(cause, "加载失败"));
      }
    })();
    return () => {
      active = false;
    };
  }, [api, kind, resourceId]);

  async function saveCurrent(): Promise<ResourceVersion | null> {
    if (!resource) return null;
    const saved = await api.updateDraft(resource, spec);
    setResource(saved);
    return saved;
  }

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      if (await saveCurrent()) onSaved();
    } catch (cause) {
      setError(messageFrom(cause, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  async function publish(): Promise<void> {
    setBusy(true);
    setError(null);
    setIssues([]);
    try {
      const saved = await saveCurrent();
      if (!saved) return;
      const validation = await api.validatePublish(saved);
      if (!validation.valid) {
        setIssues(validation.diagnostics);
        return;
      }
      await api.publishVersion(saved);
      onSaved();
      onClose();
    } catch (cause) {
      setError(messageFrom(cause, "发布失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      closable={!busy}
      footer={null}
      onCancel={onClose}
      title={kind === "model_provider" ? "编辑模型服务" : "编辑模型"}
      visible
      width={640}
    >
      <ErrorBanner message={error} />
      {resource === null ? (
        <div aria-label="模型编辑器加载中"><Spin /></div>
      ) : (
        <div aria-label="模型资源编辑器" style={{ display: "grid", gap: 14 }}>
          {kind === "model_provider" ? (
            <ProviderFields onChange={setSpec} spec={spec} />
          ) : (
            <ModelFields onChange={setSpec} providerOptions={providerOptions} spec={spec} />
          )}
          {issues.length > 0 ? (
            <div aria-label="模型发布校验问题">
              {issues.map((issue) => (
                <Typography.Paragraph key={issue} type="danger">{issue}</Typography.Paragraph>
              ))}
            </div>
          ) : null}
          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid">保存</Button>
            <Button loading={busy} onClick={() => void publish()} type="primary">发布</Button>
            <Button disabled={busy} onClick={onClose}>取消</Button>
          </Space>
        </div>
      )}
    </Modal>
  );
}

function messageFrom(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}
