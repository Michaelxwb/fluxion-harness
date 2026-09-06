import { useEffect, useState } from "react";

import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Checkbox,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Typography
} from "@douyinfe/semi-ui";

import { ErrorBanner } from "../../components/ErrorBanner";
import { StatusTag } from "../../components/StatusTag";
import type {
  ConsoleApi,
  JsonRecord,
  McpConnectionTestResult,
  ResourceVersion
} from "../../types/console";

interface McpEditorPageProps {
  readonly api: ConsoleApi;
}

/** TASK-018（§8.5）：独立 MCP Editor（`/build/mcp/:id/edit`）。
 *
 * - Transport/URL/超时编辑；测试连接（真实握手）+ 发现工具（discovered_tools
 *   勾选生成 allowed_tools 白名单，替换手填字符串数组）；
 * - MCP Tool 无手工创建入口（白名单即边界）；
 * - 仅 [保存][发布]，校验底层自动。
 */
export function McpEditorPage({ api }: McpEditorPageProps) {
  const { resourceId } = useParams<{ resourceId: string }>();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourceVersion | null>(null);
  const [specText, setSpecText] = useState("{}");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<McpConnectionTestResult | null>(null);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [publishedBaseVersion, setPublishedBaseVersion] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!resourceId) return;
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await api.getResource("mcp", resourceId);
        const target =
          loaded.status === "published"
            ? await api.createDraftFromLatest("mcp", resourceId)
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
      // specText 中间态（JSON 不完整）忽略
    }
  }

  function toggleAllowedTool(tool: string, checked: boolean): void {
    const current = Array.isArray(currentSpec().allowed_tools)
      ? (currentSpec().allowed_tools as string[])
      : [];
    const next = checked
      ? [...new Set([...current, tool])]
      : current.filter((item) => item !== tool);
    updateSpecField("allowed_tools", next);
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
    const next = await api.createDraftFromLatest("mcp", resource.resourceId);
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
      setTestResult(await api.testMcpConnection(saved.resourceId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "测试连接失败");
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
        <Typography.Title heading={3}>MCP Editor</Typography.Title>
        <Card>
          <div aria-label="编辑器加载中">
            <Spin />
          </div>
        </Card>
      </div>
    );
  }

  const spec = currentSpec();
  const allowedTools = Array.isArray(spec.allowed_tools)
    ? (spec.allowed_tools as string[])
    : [];
  const discovered = testResult?.discoveredTools ?? [];

  return (
    <div className="page-stack">
      <Space align="center">
        <Typography.Title heading={3}>MCP Editor</Typography.Title>
        {resource ? (
          <Button onClick={() => navigate("/build/capabilities/mcp")} type="tertiary">
            返回列表
          </Button>
        ) : null}
      </Space>
      <ErrorBanner message={error} />
      {notice ? <Typography.Text type="success">{notice}</Typography.Text> : null}
      {resource ? (
        <Card aria-label="MCP Editor" bodyStyle={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Space>
            <Typography.Text strong>{resource.resourceId}</Typography.Text>
            <Typography.Text type="tertiary">{`v${resource.version}`}</Typography.Text>
            <StatusTag status={resource.status} />
          </Space>

          <div>
            <Typography.Text id="mcp-edit-transport-label">连接方式</Typography.Text>
            <Select
              aria-labelledby="mcp-edit-transport-label"
              onChange={(next) => updateSpecField("transport", String(next))}
              optionList={[
                { label: "Streamable HTTP（远程服务）", value: "streamable_http" },
                { label: "stdio（本地进程）", value: "stdio" }
              ]}
              style={{ width: 280 }}
              value={String(spec.transport ?? "streamable_http")}
            />
          </div>
          <div>
            <Typography.Text>服务地址（URL）</Typography.Text>
            <Input
              aria-label="服务地址"
              onChange={(next) => updateSpecField("url", String(next))}
              placeholder="https://host/mcp"
              value={String(spec.url ?? "")}
            />
          </div>
          <div>
            <Typography.Text>连接超时（毫秒）</Typography.Text>
            <Input
              aria-label="MCP 连接超时"
              onChange={(next) => updateSpecField("timeout_ms", Number(next) || 30_000)}
              value={String(spec.timeout_ms ?? 30_000)}
            />
          </div>

          <Space>
            <Button loading={busy} onClick={() => void save()} theme="solid" type="primary">
              保存
            </Button>
            <Button loading={busy} onClick={() => void runTest()}>
              测试连接
            </Button>
            <Button loading={busy} onClick={() => setConfirmVisible(true)} type="primary">
              发布
            </Button>
          </Space>

          {testResult ? (
            <div aria-label="MCP 连接测试结果">
              {testResult.reachable ? (
                <Typography.Text type="success">
                  {`连接成功${discovered.length ? `，发现 ${discovered.length} 个远端工具` : ""}`}
                </Typography.Text>
              ) : (
                <Typography.Text type="danger">{`连接失败：${testResult.error ?? "未知错误"}`}</Typography.Text>
              )}
            </div>
          ) : null}

          {discovered.length > 0 ? (
            <div aria-label="工具白名单">
              <Typography.Text>工具白名单（勾选生成 allowed_tools；留空放行全部）</Typography.Text>
              <div style={{ display: "grid", gap: 8, paddingTop: 8 }}>
                {discovered.map((tool) => (
                  <Checkbox
                    checked={allowedTools.includes(tool)}
                    key={tool}
                    onChange={(event) => toggleAllowedTool(tool, event.target.checked === true)}
                  >
                    {tool}
                  </Checkbox>
                ))}
              </div>
              <Typography.Text type="tertiary" size="small">
                MCP Tool 无手工创建入口；白名单勾选即授权边界。
              </Typography.Text>
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
        title="确认发布 MCP"
        visible={confirmVisible}
      >
        <Typography.Text>发布后版本不可变；引用此 MCP 的 Agent 用户授权链需重新校验。</Typography.Text>
      </Modal>
    </div>
  );
}
