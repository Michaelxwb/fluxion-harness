import { useEffect, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";
import { Button, Card, Input, Modal, Select, Space, Spin, TextArea, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  JsonRecord,
  ResourceVersion,
  SkillPackageInfo
} from "../../types/console";
import { CreateSkillModal } from "./CreateSkillModal";

interface SkillEditorPageProps {
  readonly api: ConsoleApi;
}

interface SkillFormValue {
  readonly name: string;
  readonly instructions: string;
  readonly requiredCapabilities: readonly string[];
}

/** TASK-016（§8.3）：独立 Skill Editor（`/build/skills/:id/edit`）。
 *
 * - published 资源打开即自动 working draft（FEAT-F06 无感化）；
 * - 复杂内容编辑：instructions（注入 system prompt）+ required_capabilities；
 * - 仅 [保存][发布]，校验底层自动（保存=基础校验，发布=完整校验）。
 */
export function SkillEditorPage({ api }: SkillEditorPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [value, setValue] = useState<SkillFormValue>({ name: "", instructions: "", requiredCapabilities: [] });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publishIssues, setPublishIssues] = useState<readonly string[] | null>(null);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);
  const [confirmPublish, setConfirmPublish] = useState<(() => Promise<void>) | null>(null);
  const [packageInfo, setPackageInfo] = useState<SkillPackageInfo | null>(null);
  const [reuploadVisible, setReuploadVisible] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await api.getResource("skill", resourceId);
        const target =
          loaded.status === "published"
            ? await api.createDraftFromLatest("skill", resourceId)
            : loaded;
        if (!active) return;
        setResource(target);
        try {
          const info = await api.getSkillPackage(resourceId, target.version);
          if (active) setPackageInfo(info);
        } catch {
          if (active) setPackageInfo(null);
        }
        setPublishedBaseVersion(loaded.status === "published" ? loaded.version : undefined);
        setValue({
          name: String(target.spec.name ?? ""),
          instructions: String(target.spec.instructions ?? ""),
          requiredCapabilities: Array.isArray(target.spec.required_capabilities)
            ? (target.spec.required_capabilities as string[])
            : []
        });
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId, reloadKey]);

  function spec(): JsonRecord {
    return {
      name: value.name,
      instructions: value.instructions,
      required_capabilities: [...value.requiredCapabilities],
      visibility: resource?.spec.visibility === "private" ? "private" : "public"
    };
  }

  async function save(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, spec());
      setResource(saved);
      setNotice("已保存");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  /** S-06：已发布版本不可变——写前若工作态已是 published，先 fork 新 Draft
   * 再写，否则直接 update 会 409。draft 态零额外请求直接返回。
   * 注意只切换 resource，不碰表单 value（表单持有最新未存内容）。
   */
  async function ensureDraft(): Promise<ResourceVersion> {
    if (!resource) throw new Error("资源尚未加载");
    if (resource.status !== "published") return resource;
    const next = await api.createDraftFromLatest("skill", resource.resourceId);
    setResource(next);
    return next;
  }

  async function publish(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, spec());
      const validation = await api.validatePublish(saved);
      if (!validation.valid) {
        setResource(saved);
        setPublishIssues(validation.diagnostics);
        setNotice("无法发布");
        return;
      }
      setPublishIssues(null);
      const result = await api.publishVersion(saved, { expectedBaseVersion: publishedBaseVersion });
      // S-06：发布成功即为 published 态；后续保存经 ensureDraft fork 新版。
      setResource({ ...saved, status: "published" });
      setPublishedBaseVersion(result.version);
      setConfirmVisible(false);
      setNotice(`已发布 ${result.version}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发布失败");
    } finally {
      setBusy(false);
    }
  }

  function requestPublish(): void {
    setConfirmPublish(() => publish);
    setConfirmVisible(true);
  }

  if (loading) {
    return (
      <div className="page-stack">
        <Typography.Title heading={3}>Skill Editor</Typography.Title>
        <Card>
          <div aria-label="编辑器加载中">
            <Spin />
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <Space align="center">
        <Typography.Title heading={3}>Skill Editor</Typography.Title>
        {resource ? (
          <Button onClick={() => navigate("/build/capabilities/skill")} type="tertiary">
            返回列表
          </Button>
        ) : null}
      </Space>
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {resource ? (
        <Card aria-label="Skill Editor" bodyStyle={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Space>
            <Typography.Text strong>{resource.resourceId}</Typography.Text>
            <Typography.Text type="tertiary">{`v${resource.version}`}</Typography.Text>
            <StatusTag status={resource.status} />
          </Space>
          {packageInfo ? (
            <Card aria-label="Skill Package 信息" bodyStyle={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <Space>
                <Typography.Text strong>来源 Package</Typography.Text>
                <Typography.Text type="tertiary">{`artifact ${packageInfo.artifactHash?.slice(0, 12) ?? "—"}`}</Typography.Text>
                <Button onClick={() => setReuploadVisible(true)} size="small">
                  重新上传
                </Button>
              </Space>
              <Typography.Text type="tertiary" size="small">
                {`知识文件：${knowledgeFiles(packageInfo).join("、") || "无"}`}
              </Typography.Text>
              <Typography.Text type="warning" size="small">
                直接编辑做法说明将与 Package 分叉；建议改包后重新上传。
              </Typography.Text>
            </Card>
          ) : null}
          <div>
            <Typography.Text>技能名称 *</Typography.Text>
            <Input
              aria-label="技能名称"
              onChange={(next) => setValue((current) => ({ ...current, name: String(next) }))}
              value={value.name}
            />
          </div>
          <div>
            <Typography.Text>做法说明</Typography.Text>
            <TextArea
              aria-label="做法说明"
              onChange={(next) => setValue((current) => ({ ...current, instructions: String(next) }))}
              placeholder="固化给助手的任务做法；注入 system prompt"
              rows={8}
              value={value.instructions}
            />
          </div>
          <div>
            <Typography.Text id="skill-required-capabilities-label">所需能力（可选）</Typography.Text>
            <Select
              aria-labelledby="skill-required-capabilities-label"
              filter
              multiple
              onChange={(next) =>
                setValue((current) => ({
                  ...current,
                  requiredCapabilities: Array.isArray(next) ? next.map(String) : []
                }))
              }
              placeholder="引用 Agent 已声明的能力（不隐式扩权）"
              style={{ width: "100%" }}
              value={[...value.requiredCapabilities]}
            />
          </div>
          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid" type="primary">
              保存
            </Button>
            <Button loading={busy} onClick={requestPublish} type="primary">
              发布
            </Button>
          </Space>
          {publishIssues?.length ? (
            <div aria-label="发布校验问题">
              <Typography.Text type="danger">无法发布，发现 {publishIssues.length} 个问题：</Typography.Text>
              <ul>
                {publishIssues.map((issue) => (
                  <li key={issue}>
                    <Typography.Text type="danger">{issue}</Typography.Text>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>
      ) : null}
      <CreateSkillModal
        api={api}
        onClose={() => setReuploadVisible(false)}
        onCreated={(created) => {
          setReuploadVisible(false);
          setNotice(`已上传发布 ${created.version}`);
          setReloadKey((key) => key + 1);
        }}
        visible={reuploadVisible}
      />
      <Modal
        cancelText="取 消"
        okText="确认发布"
        okButtonProps={{ loading: busy }}
        onOk={() => void (confirmPublish?.() ?? Promise.resolve())}
        onCancel={() => setConfirmVisible(false)}
        title="确认发布 Skill"
        visible={confirmVisible}
      >
        <Typography.Text>
          发布后版本不可变；引用此 Skill 的 Workflow/Agent 需重新校验。
        </Typography.Text>
      </Modal>
    </div>
  );
}

function knowledgeFiles(info: SkillPackageInfo): readonly string[] {
  const files = info.knowledgeManifest.files;
  return Array.isArray(files) ? files.filter((item): item is string => typeof item === "string") : [];
}
