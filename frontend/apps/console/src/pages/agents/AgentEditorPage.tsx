import { useEffect, useState } from "react";

import { useParams } from "react-router-dom";
import { Card, Spin, Typography } from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import type { ConsoleApi, ResourceVersion } from "../../types/console";
import { AgentEditorForm } from "./AgentEditorForm";
import {
  EMPTY_AGENT_EDITOR_VALUE,
  editorSpec,
  editorValueFrom,
  type AgentEditorValue
} from "./agentEditorModel";

interface AgentEditorPageProps {
  readonly api: ConsoleApi;
}

/** TASK-014：Agent 专属 Editor（`/build/agents/:id/edit`）。
 *
 * - 编辑从列表「编辑」进入；详情（SideSheet，TASK-013）只读不发编辑。
 * - 已发布资源自动经 `createDraftFromLatest` 产生 working draft（用户无感），
 *   删除「创建草稿/保存草稿」显式概念。
 * - 核心按钮收敛为 [保存] [发布]（发布前完整校验由后端 `:validate-publish` 承担）。
 */
export function AgentEditorPage({ api }: AgentEditorPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [value, setValue] = useState<AgentEditorValue>(EMPTY_AGENT_EDITOR_VALUE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [publishIssues, setPublishIssues] = useState<readonly string[] | null>(null);
  const [latestTraceId, setLatestTraceId] = useState<string | null>(null);
  // ADR-A011：乐观并发检查 base——published 资源编辑时记录其当前 published 版本，
  // 发布携带 expected_base_version 防止并发覆盖（首次发布无 base 则为 undefined）。
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    void (async () => {
      try {
        const loaded = await api.getResource("agent_definition", resourceId);
        // 已发布 → 自动 working draft（用户无感）；draft → 直接编辑
        const draft =
          loaded.status === "published"
            ? await api.createDraftFromLatest("agent_definition", resourceId)
            : loaded;
        if (!active) return;
        setResource(draft);
        // ADR-A011：published 资源编辑记录乐观并发 base（fork 前的 published 版本）
        setPublishedBaseVersion(loaded.status === "published" ? loaded.version : undefined);
        setValue(editorValueFrom(draft));
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      }
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId]);

  async function save(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, editorSpec(target, value));
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
   */
  async function ensureDraft(): Promise<ResourceVersion> {
    if (!resource) throw new Error("资源尚未加载");
    if (resource.status !== "published") return resource;
    const next = await api.createDraftFromLatest("agent_definition", resource.resourceId);
    setResource(next);
    return next;
  }

  async function publish(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      // 发布前自动完整校验（TASK-009 返工）：先保存当前表单为 working draft，
      // 对保存后的版本做完整校验——校验对象是即将发布的 spec，用户刚加入的
      // 非法引用不可绕过预检；失败渲染可操作问题清单，不静默发布
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, editorSpec(target, value));
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
      setNotice("已发布");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发布失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <Typography.Title heading={3}>编辑智能体</Typography.Title>
      <ErrorBanner message={error} />
      {resource === null ? (
        <Card>
          <div aria-label="编辑器加载中">
            <Spin />
          </div>
        </Card>
      ) : (
        <Card aria-label="智能体编辑器">
          <AgentEditorForm
            agentId={resource.resourceId}
            api={api}
            busy={busy}
            notice={notice}
            onChange={(change) => setValue((current) => ({ ...current, ...change }))}
            onPublish={() => void publish()}
            onSave={() => void save()}
            onTestCompleted={setLatestTraceId}
            publishIssues={publishIssues}
            value={value}
            latestTraceId={latestTraceId}
          />
        </Card>
      )}
    </div>
  );
}
