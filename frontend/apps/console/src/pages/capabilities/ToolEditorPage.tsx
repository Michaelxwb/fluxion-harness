import { useEffect, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  TextArea,
  Typography
} from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  JsonRecord,
  ResourceVersion,
  ToolCallTestResult
} from "../../types/console";

interface ToolEditorPageProps {
  readonly api: ConsoleApi;
}

/** TASK-017（§8.4）：独立 Tool Editor（`/build/tools/:id/edit`）。
 *
 * 类型分支配置：http_api（URL/Method/Headers）| platform_service（service_name/
 * operation）；timeout/fail_policy 显式（规则 18）；Test Call 真实出站。
 * 仅 [保存][发布]，校验底层自动。规则 12：Tool 是 Agent-facing Adapter，
 * 业务能力仍走 Capability Contract（capability_ref 字段保留）。
 */
export function ToolEditorPage({ api }: ToolEditorPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [specText, setSpecText] = useState("{}");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ToolCallTestResult | null>(null);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await api.getResource("tool", resourceId);
        const target =
          loaded.status === "published"
            ? await api.createDraftFromLatest("tool", resourceId)
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
      setTestResult(null);
    } catch {
      // specText 中间态（JSON 不完整）忽略
    }
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
   * 注意只切换 resource，不碰 specText（表单持有最新未存内容）。
   */
  async function ensureDraft(): Promise<ResourceVersion> {
    if (!resource) throw new Error("资源尚未加载");
    if (resource.status !== "published") return resource;
    const next = await api.createDraftFromLatest("tool", resource.resourceId);
    setResource(next);
    return next;
  }

  async function runTest(): Promise<void> {
    if (!resource) return;
    setBusy(true);
    setError(null);
    try {
      const target = await ensureDraft();
      const saved = await api.updateDraft(target, currentSpec());
      setResource(saved);
      setTestResult(await api.testToolCall(saved.resourceId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "测试调用失败");
    } finally {
      setBusy(false);
    }
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
      // S-06：发布成功即为 published 态（不再持有“草稿”假象）；后续保存经
      // ensureDraft fork 新版。此处不直接 fork——S-C108 要求发布后 latest
      // 仍是刚发布的版本。
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
        <Typography.Title heading={3}>Tool Editor</Typography.Title>
        <Card>
          <div aria-label="编辑器加载中">
            <Spin />
          </div>
        </Card>
      </div>
    );
  }

  const spec = currentSpec();
  const toolKind = spec.tool_kind === "platform_service" ? "platform_service" : "http_api";

  return (
    <div className="page-stack">
      <Space align="center">
        <Typography.Title heading={3}>Tool Editor</Typography.Title>
        {resource ? (
          <Button onClick={() => navigate("/build/capabilities/tool")} type="tertiary">
            返回列表
          </Button>
        ) : null}
      </Space>
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {resource ? (
        <Card aria-label="Tool Editor" bodyStyle={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Space>
            <Typography.Text strong>{resource.resourceId}</Typography.Text>
            <Typography.Text type="tertiary">{`v${resource.version}`}</Typography.Text>
            <StatusTag status={resource.status} />
          </Space>

          <div>
            <Typography.Text>调用地址（URL）</Typography.Text>
            <Input
              aria-label="调用地址"
              disabled={toolKind !== "http_api"}
              onChange={(next) => updateSpecField("url", String(next))}
              placeholder="https://host/path"
              value={String(spec.url ?? "")}
            />
          </div>
          <div>
            <Typography.Text id="tool-method-label">HTTP 方法</Typography.Text>
            <Select
              aria-labelledby="tool-method-label"
              disabled={toolKind !== "http_api"}
              onChange={(next) => updateSpecField("method", String(next))}
              optionList={["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => ({ label: m, value: m }))}
              style={{ width: 160 }}
              value={String(spec.method ?? "GET")}
            />
          </div>
          <div>
            <Typography.Text>调用超时（毫秒，规则 18 显式超时）</Typography.Text>
            <Input
              aria-label="调用超时"
              onChange={(next) => updateSpecField("timeout_ms", Number(next) || 30_000)}
              value={String(spec.timeout_ms ?? 30_000)}
            />
          </div>
          <div>
            <Typography.Text id="tool-fail-policy-label">失败策略</Typography.Text>
            <Select
              aria-labelledby="tool-fail-policy-label"
              onChange={(next) => updateSpecField("fail_policy", String(next))}
              optionList={[
                { label: "fail_closed（失败即终止）", value: "fail_closed" },
                { label: "fail_open（失败降级继续）", value: "fail_open" }
              ]}
              style={{ width: 280 }}
              value={String(spec.fail_policy ?? "fail_closed")}
            />
          </div>
          <div>
            <Typography.Text>描述</Typography.Text>
            <TextArea
              aria-label="工具描述编辑"
              onChange={(next) => updateSpecField("description", String(next))}
              rows={2}
              value={String(spec.description ?? "")}
            />
          </div>

          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid" type="primary">
              保存
            </Button>
            <Button onClick={() => void runTest()}>测试调用</Button>
            <Button loading={busy} onClick={() => setConfirmVisible(true)} type="primary">
              发布
            </Button>
          </Space>

          {testResult ? (
            <div aria-label="测试调用结果">
              {testResult.reachable ? (
                <Typography.Text type="success">
                  {`测试调用成功${testResult.statusCode ? `（HTTP ${testResult.statusCode}）` : ""}`}
                </Typography.Text>
              ) : (
                <Typography.Text type="danger">{`测试调用失败：${testResult.error ?? "未知错误"}`}</Typography.Text>
              )}
              {testResult.bodyExcerpt ? (
                <Typography.Paragraph type="tertiary" style={{ marginBottom: 0 }}>
                  {testResult.bodyExcerpt}
                </Typography.Paragraph>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}
      <Modal
        cancelText="取 消"
        okText="确认发布"
        okButtonProps={{ loading: busy }}
        onOk={() => void publish()}
        onCancel={() => setConfirmVisible(false)}
        title="确认发布 Tool"
        visible={confirmVisible}
      >
        <Typography.Text>发布后版本不可变；引用此 Tool 的 Agent 需重新校验授权链。</Typography.Text>
      </Modal>
    </div>
  );
}
