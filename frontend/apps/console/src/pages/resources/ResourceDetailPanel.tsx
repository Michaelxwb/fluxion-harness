import { useEffect, useRef, useState } from "react";

import { Button, Card, Descriptions, Space, Spin, Typography } from "@douyinfe/semi-ui";
import { IconPlay, IconPlus, IconSave } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { SpecForm } from "../../components/SpecForm";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, JsonSchemaNode, PageData, ResourceType, ResourceVersion } from "../../types/console";
import { type ConfirmAction, ResourceActionModal } from "./ResourceActionModal";
import { ResourceVersionsPanel, VERSION_PAGE_SIZE } from "./ResourceVersionsPanel";

interface ResourceDetailPanelProps {
  readonly api: ConsoleApi;
  readonly resourceType: ResourceType;
  readonly resourceId: string;
}

export function ResourceDetailPanel({ api, resourceType, resourceId }: ResourceDetailPanelProps) {
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [spec, setSpec] = useState<JsonRecord>({});
  const [schema, setSchema] = useState<JsonSchemaNode | null>(null);
  const [versions, setVersions] = useState<PageData<ResourceVersion> | null>(null);
  const [versionPage, setVersionPage] = useState(1);
  const [diagnostic, setDiagnostic] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [loading, setLoading] = useState(true);
  const versionsRequestId = useRef(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setNotice(null);
    // ADR-012：草稿编辑表单的 schema 同样取自后端 spec model（单一真相源）。
    void api.getResourceSchema(resourceType).then(
      (loaded) => {
        if (active) setSchema(loaded);
      },
      (cause: unknown) => {
        if (active) setError(toErrorMessage(cause));
      }
    );
    void api.getResource(resourceType, resourceId).then(
      (loaded) => {
        if (!active) return;
        applyResource(loaded);
        setLoading(false);
        void loadVersions(loaded, 1);
      },
      (cause: unknown) => {
        if (!active) return;
        setError(toErrorMessage(cause));
        setLoading(false);
      }
    );
    return () => {
      active = false;
    };
  }, [api, resourceType, resourceId]);

  async function loadVersions(target: ResourceVersion, page: number): Promise<void> {
    // 与 getResource 的 active 守卫同义：快速切换资源时，旧请求的 setVersions 不应
    // 覆盖新资源的版本列表。用单调 token 记 latest 调用，过期结果丢弃。
    const requestId = ++versionsRequestId.current;
    setVersionPage(page);
    const result = await api.listVersions(target.resourceType, target.resourceId, {
      page,
      pageSize: VERSION_PAGE_SIZE
    });
    if (requestId !== versionsRequestId.current) return;
    setVersions(result);
  }

  function applyResource(next: ResourceVersion): void {
    setResource(next);
    setSpec(next.spec);
    setDiagnostic(null);
  }

  async function createDraft(): Promise<void> {
    if (!resource) return;
    await runAction(async () => {
      const draft = await api.createDraftFromLatest(resource.resourceType, resource.resourceId);
      applyResource(draft);
      await loadVersions(draft, 1);
      setNotice(`草稿 ${draft.version} 已创建`);
    });
  }

  async function saveDraft(): Promise<void> {
    if (!resource) return;
    await runAction(async () => {
      const updated = await api.updateDraft(resource, spec);
      applyResource(updated);
      await loadVersions(updated, versionPage);
      setNotice("草稿已保存");
    });
  }

  async function validateDraft(): Promise<void> {
    if (!resource) return;
    await runAction(async () => {
      const result = await api.validateDraft(resource);
      setDiagnostic(result.diagnostics.join("；"));
      setNotice(result.valid ? "校验完成" : "校验失败");
    });
  }

  async function runAction(action: () => Promise<void>): Promise<void> {
    try {
      await action();
      setError(null);
    } catch (cause) {
      setError(toErrorMessage(cause));
    }
  }

  async function handleConfirmDone(message: string, target: ResourceVersion): Promise<void> {
    setNotice(message);
    const latest = await api.getResource(target.resourceType, target.resourceId);
    applyResource(latest);
    await loadVersions(latest, 1);
  }

  return (
    <div className="page-stack">
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {loading ? (
        <div aria-label="资源详情 loading" role="status">
          <Spin size="large" />
        </div>
      ) : null}
      {!loading && resource ? (
        <>
          <SpecEditor
            diagnostic={diagnostic}
            onCreateDraft={() => void createDraft()}
            onPublish={(target) => setConfirmAction({ resource: target, type: "publish" })}
            onSave={() => void saveDraft()}
            onSpecChange={setSpec}
            onValidate={() => void validateDraft()}
            resource={resource}
            schema={schema}
            spec={spec}
          />
          <ResourceVersionsPanel
            onRollback={(target, version) =>
              setConfirmAction({ resource: target, targetVersion: version, type: "rollback" })
            }
            onVersionPageChange={(page) => void loadVersions(resource, page)}
            resource={resource}
            versionPage={versionPage}
            versions={versions}
          />
        </>
      ) : null}
      <ResourceActionModal action={confirmAction} api={api} onClose={() => setConfirmAction(null)} onDone={handleConfirmDone} />
    </div>
  );
}

function SpecEditor({
  diagnostic,
  onCreateDraft,
  onPublish,
  onSave,
  onSpecChange,
  onValidate,
  resource,
  schema,
  spec
}: {
  readonly diagnostic: string | null;
  readonly resource: ResourceVersion;
  readonly schema: JsonSchemaNode | null;
  readonly spec: JsonRecord;
  readonly onCreateDraft: () => void;
  readonly onPublish: (resource: ResourceVersion) => void;
  readonly onSave: () => void;
  readonly onSpecChange: (next: JsonRecord) => void;
  readonly onValidate: () => void;
}) {
  return (
    <Card aria-label="规格编辑" bodyStyle={{ display: "flex", flexDirection: "column", gap: 12 }} title="规格编辑">
      <Descriptions row>
        <Descriptions.Item itemKey="资源">{resource.resourceId}</Descriptions.Item>
        <Descriptions.Item itemKey="版本">{resource.version}</Descriptions.Item>
        <Descriptions.Item itemKey="状态"><StatusTag status={resource.status} /></Descriptions.Item>
      </Descriptions>
      <Typography.Text type="tertiary">编辑草稿：结构化表单或高级 JSON 模式，保存 → 校验 → 发布。</Typography.Text>
      {schema ? (
        <SpecForm onChange={onSpecChange} schema={schema} spec={spec} />
      ) : (
        <Spin aria-label="加载表单" />
      )}
      {diagnostic ? <Typography.Text type="success">{diagnostic}</Typography.Text> : null}
      <Space wrap>
        <Button aria-label="创建草稿" icon={<IconPlus />} onClick={onCreateDraft}>创建草稿</Button>
        <Button aria-label="保存草稿" icon={<IconSave />} onClick={onSave} type="primary">保存草稿</Button>
        <Button aria-label="校验" icon={<IconPlay />} onClick={onValidate}>校验</Button>
        <Button aria-label="发布" icon={<IconPlay />} onClick={() => onPublish(resource)} theme="solid" type="warning">
          发布
        </Button>
      </Space>
    </Card>
  );
}

function toErrorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : "未知错误";
}
