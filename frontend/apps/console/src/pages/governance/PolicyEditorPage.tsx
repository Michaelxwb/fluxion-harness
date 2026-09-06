import { useEffect, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Typography
} from "@douyinfe/semi-ui";
import { IconPlus, IconDelete } from "@douyinfe/semi-icons";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import type { ConsoleApi, JsonRecord, ResourceVersion } from "../../types/console";

interface PolicyEditorPageProps {
  readonly api: ConsoleApi;
}

/** TASK-022（§8.11）：独立 Policy Editor（`/build/policies/:id/edit`）。
 *
 * 结构化规则编辑（typed 表单）：allowed_tools/denied_tools 逐条增删（Tag 呈现，
 * 非 raw JSON）。规则 12 边界：Policy 是运行时工具授权真读字段（ADR-012），
 * 发布后经 tenant Binding 生效（生效链路提示）。仅 [保存][发布]。
 */
export function PolicyEditorPage({ api }: PolicyEditorPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [specText, setSpecText] = useState("{}");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allowedInput, setAllowedInput] = useState("");
  const [deniedInput, setDeniedInput] = useState("");
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await api.getResource("policy", resourceId);
        const target =
          loaded.status === "published"
            ? await api.createDraftFromLatest("policy", resourceId)
            : loaded;
        if (!active) return;
        setResource(target);
        setPublishedBaseVersion(loaded.status === "published" ? loaded.version : undefined);
        setSpecText(JSON.stringify(target.spec, null, 2));
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "加载失败");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [api, resourceId]);

  function currentSpec(): JsonRecord {
    try {
      return JSON.parse(specText) as JsonRecord;
    } catch {
      return resource?.spec ?? {};
    }
  }

  function updateSpecField(field: string, value: unknown): void {
    try {
      const parsed = JSON.parse(specText) as Record<string, unknown>;
      parsed[field] = value;
      setSpecText(JSON.stringify(parsed, null, 2));
    } catch {
      // 中间态忽略
    }
  }

  function addTool(field: "allowed_tools" | "denied_tools", value: string): void {
    const trimmed = value.trim();
    if (!trimmed) return;
    const current = Array.isArray(currentSpec()[field])
      ? (currentSpec()[field] as string[])
      : [];
    if (!current.includes(trimmed)) updateSpecField(field, [...current, trimmed]);
  }

  function removeTool(field: "allowed_tools" | "denied_tools", value: string): void {
    const current = Array.isArray(currentSpec()[field])
      ? (currentSpec()[field] as string[])
      : [];
    updateSpecField(field, current.filter((item) => item !== value));
  }

  async function save(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, currentSpec());
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
    const next = await api.createDraftFromLatest("policy", resource.resourceId);
    setResource(next);
    return next;
  }

  async function publish(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, currentSpec());
      const validation = await api.validatePublish(saved);
      if (!validation.valid) {
        setResource(saved);
        setError(`无法发布：${validation.diagnostics.join("；")}`);
        setConfirmVisible(false);
        return;
      }
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

  if (loading) {
    return (
      <div className="page-stack">
        <Typography.Title heading={3}>Policy Editor</Typography.Title>
        <Card>
          <div aria-label="编辑器加载中">
            <Spin />
          </div>
        </Card>
      </div>
    );
  }

  const spec = currentSpec();
  const allowedTools = Array.isArray(spec.allowed_tools) ? (spec.allowed_tools as string[]) : [];
  const deniedTools = Array.isArray(spec.denied_tools) ? (spec.denied_tools as string[]) : [];

  return (
    <div className="page-stack">
      <Space align="center">
        <Typography.Title heading={3}>Policy Editor</Typography.Title>
        {resource ? (
          <Button onClick={() => navigate("/governance/policies")} type="tertiary">
            返回列表
          </Button>
        ) : null}
      </Space>
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {resource ? (
        <Card aria-label="Policy Editor" bodyStyle={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Space>
            <Typography.Text strong>{resource.resourceId}</Typography.Text>
            <Typography.Text type="tertiary">{`v${resource.version}`}</Typography.Text>
            <StatusTag status={resource.status} />
          </Space>

          <div>
            <Typography.Text>工具白名单（非空时仅放行所列）</Typography.Text>
            <Space style={{ paddingTop: 8 }}>
              <Input
                aria-label="工具白名单输入"
                onChange={(value) => setAllowedInput(String(value))}
                placeholder="tool:mailer@1"
                style={{ width: 320 }}
                value={allowedInput}
              />
              <Button
                aria-label="添加白名单工具"
                icon={<IconPlus />}
                onClick={() => {
                  addTool("allowed_tools", allowedInput);
                  setAllowedInput("");
                }}
              >
                添加
              </Button>
            </Space>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, paddingTop: 8 }}>
              {allowedTools.map((tool) => (
                <Tag
                  key={tool}
                  closable
                  onClick={() => removeTool("allowed_tools", tool)}
                  onClose={() => removeTool("allowed_tools", tool)}
                >
                  {tool}
                </Tag>
              ))}
              {allowedTools.length === 0 ? (
                <Typography.Text type="tertiary">留空则不限定工具面</Typography.Text>
              ) : null}
            </div>
          </div>

          <div>
            <Typography.Text>工具黑名单（始终优先拒绝）</Typography.Text>
            <Space style={{ paddingTop: 8 }}>
              <Input
                aria-label="工具黑名单输入"
                onChange={(value) => setDeniedInput(String(value))}
                placeholder="tool:dangerous@1"
                style={{ width: 320 }}
                value={deniedInput}
              />
              <Button
                aria-label="添加黑名单工具"
                icon={<IconDelete />}
                onClick={() => {
                  addTool("denied_tools", deniedInput);
                  setDeniedInput("");
                }}
              >
                添加
              </Button>
            </Space>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, paddingTop: 8 }}>
              {deniedTools.map((tool) => (
                <Tag
                  color="red"
                  key={tool}
                  onClose={() => removeTool("denied_tools", tool)}
                >
                  {tool}
                </Tag>
              ))}
              {deniedTools.length === 0 ? (
                <Typography.Text type="tertiary">无拒绝工具</Typography.Text>
              ) : null}
            </div>
          </div>

          <Typography.Text type="tertiary">
            生效链路：发布 → 经「治理 / 绑定管理」绑定到租户（subject_type=tenant）→ 运行期
            TenantPolicy 工具面收口（Grant ∩ Allowlist ∩ TenantPolicy）。
          </Typography.Text>

          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid" type="primary">
              保存
            </Button>
            <Button loading={busy} onClick={() => setConfirmVisible(true)} type="primary">
              发布
            </Button>
          </Space>
        </Card>
      ) : null}
      <Modal
        cancelText="取 消"
        okText="确认发布"
        okButtonProps={{ loading: busy }}
        onOk={() => void publish()}
        onCancel={() => setConfirmVisible(false)}
        title="确认发布授权规则"
        visible={confirmVisible}
      >
        <Typography.Text>
          发布后版本不可变；策略影响租户内全部 Agent 的可调用工具面，请确认影响范围。
        </Typography.Text>
      </Modal>
    </div>
  );
}
