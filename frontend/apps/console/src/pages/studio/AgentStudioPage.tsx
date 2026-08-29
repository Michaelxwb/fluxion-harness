import { useEffect, useMemo, useState } from "react";

import {
  Banner,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography
} from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import type {
  CapabilitySelection,
  CapabilitySelectionType,
  ConsoleApi,
  ResourceVersion
} from "../../types/console";

interface AgentStudioPageProps {
  readonly api: ConsoleApi;
  /** 编辑场景：直接载入该 agent（Phase 1 以新建为主，保留扩展位）。 */
  readonly initialAgentId?: string;
}

const CAPABILITY_TYPE_LABELS: Record<CapabilitySelectionType, string> = {
  skill: "Skill",
  tool: "Tool",
  mcp: "MCP"
};

/** closure TASK-008（P1C-04）：typed 能力选择器——产出 CapabilitySelection
 * 三元组（type + capabilityRef + versionPin），展示「名称 + 类型 + 版本」。 */
export function CapabilityPicker({
  api,
  selected,
  onChange
}: {
  readonly api: ConsoleApi;
  readonly selected: readonly CapabilitySelection[];
  readonly onChange: (next: readonly CapabilitySelection[]) => void;
}) {
  const [options, setOptions] = useState<
    readonly { id: string; kind: CapabilitySelectionType; version: string; label: string }[]
  >([]);
  useEffect(() => {
    void (async () => {
      const merged: { id: string; kind: CapabilitySelectionType; version: string; label: string }[] = [];
      for (const kind of ["skill", "tool", "mcp"] as const) {
        const items = await api.listVisibleResources(kind);
        merged.push(
          ...items.map((i) => ({
            id: i.resourceId,
            kind,
            version: i.currentVersion,
            label: i.displayName || i.resourceId
          }))
        );
      }
      setOptions(merged);
    })();
  }, [api]);

  const toggle = (option: { id: string; kind: CapabilitySelectionType; version: string }) => {
    const exists = selected.some(
      (item) => item.type === option.kind && item.capabilityRef === option.id
    );
    onChange(
      exists
        ? selected.filter((item) => !(item.type === option.kind && item.capabilityRef === option.id))
        : [
            ...selected,
            { type: option.kind, capabilityRef: option.id, versionPin: option.version }
          ]
    );
  };

  return (
    <div aria-label="能力绑定选择">
      {options.map((option) => {
        const checked = selected.some(
          (item) => item.type === option.kind && item.capabilityRef === option.id
        );
        return (
          <label key={`${option.kind}:${option.id}`} style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={checked}
              onChange={() => toggle(option)}
            />
            {`${option.label} ${CAPABILITY_TYPE_LABELS[option.kind]} v${option.version}`}
          </label>
        );
      })}
    </div>
  );
}

interface ModelOption {
  readonly id: string;
  readonly label: string;
}

/** TASK-015 / FEAT-F03：Agent Studio——构建/预览/试跑三段式工作台。 */
export function AgentStudioPage({ api, initialAgentId }: AgentStudioPageProps) {
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("你是客服。");
  const [owner, setOwner] = useState("builder-1");
  const [instructions, setInstructions] = useState("");
  const [modelId, setModelId] = useState("");
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [modelCreateOpen, setModelCreateOpen] = useState(false);
  const [newModelId, setNewModelId] = useState("");
  const [newModelName, setNewModelName] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [runInput, setRunInput] = useState("");
  const [runOutput, setRunOutput] = useState("");
  const [runError, setRunError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [savedAgentId, setSavedAgentId] = useState<string | null>(null);
  const [runtimeProfileId, setRuntimeProfileId] = useState("");
  const [profiles, setProfiles] = useState<readonly ModelOption[]>([]);
  const [capabilities, setCapabilities] = useState<readonly CapabilitySelection[]>([]);
  const [memoryPolicyRef, setMemoryPolicyRef] = useState("");
  const [personalizationPolicyRef, setPersonalizationPolicyRef] = useState("");
  // TASK-017（C402 UX 深化）：版本管理（列表/对比/回滚入口）
  const [versions, setVersions] = useState<readonly ResourceVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [versionsReloadKey, setVersionsReloadKey] = useState(0);
  const [compareTarget, setCompareTarget] = useState<ResourceVersion | null>(null);
  const [rollbackNotice, setRollbackNotice] = useState<string | null>(null);

  useEffect(() => {
    void api.listVisibleResources("model").then((items) => {
      setModels(items.map((m) => ({ id: m.resourceId, label: m.displayName || m.resourceId })));
    });
    void api.listVisibleResources("runtime_profile").then((items) => {
      setProfiles(items.map((p) => ({ id: p.resourceId, label: p.displayName || p.resourceId })));
    });
  }, [api]);

  // TASK-017：保存后（或经 initialAgentId 深链）加载版本列表。
  const versionAgentId = savedAgentId ?? initialAgentId ?? null;
  useEffect(() => {
    if (!versionAgentId) {
      setVersions(null);
      return;
    }
    let active = true;
    setVersionsError(null);
    void api
      .listVersions("agent_definition", versionAgentId, { page: 1, pageSize: 20 })
      .then((page) => {
        if (active) setVersions(page.items);
      })
      .catch((cause: unknown) => {
        if (active) {
          setVersionsError(cause instanceof Error ? cause.message : "未知错误");
        }
      });
    return () => {
      active = false;
    };
  }, [api, versionAgentId, versionsReloadKey]);

  const rollback = async (target: ResourceVersion) => {
    if (!versionAgentId) return;
    try {
      const result = await api.rollbackVersion(target, target.version);
      setVersionsReloadKey((key) => key + 1);
      setRollbackNotice(`已回滚到 ${result.targetVersion}（新版本 ${result.newVersion}）`);
    } catch (cause) {
      setRollbackNotice(`回滚失败：${cause instanceof Error ? cause.message : "未知错误"}`);
    }
  };

  const previewText = useMemo(
    () => [systemPrompt, instructions].filter((v) => v.trim()).join("\n\n"),
    [systemPrompt, instructions]
  );

  const saveDraft = async () => {
    const nextErrors: Record<string, string> = {};
    if (!name.trim()) nextErrors.name = "智能体名：必填";
    if (!systemPrompt.trim()) nextErrors.systemPrompt = "系统提示词：必填";
    if (!owner.trim()) nextErrors.owner = "归属：必填";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      setSavedNotice(null);
      return;
    }
    const resourceId = `studio_${Date.now().toString(36)}`;
    await api.createResource({
      resourceType: "agent_definition",
      resourceId,
      version: "1",
      visibility: "private",
      spec: {
        name,
        system_prompt: systemPrompt,
        owner,
        instructions,
        model_ref: { id: modelId || "dev.echo", version: "1" },
        // closure TASK-007（P1C-03）：五段字段完整落 spec——此前四处全部丢失。
        ...(runtimeProfileId
          ? { runtime_profile_ref: { id: runtimeProfileId, version: "1" } }
          : {}),
        capabilities: capabilities.map((item) => ({
          type: item.type,
          capability_ref: item.capabilityRef,
          version_pin: item.versionPin
        })),
        ...(memoryPolicyRef
          ? { memory_policy_ref: { id: memoryPolicyRef, version: "1" } }
          : {}),
        ...(personalizationPolicyRef
          ? { personalization_policy_ref: { id: personalizationPolicyRef, version: "1" } }
          : {})
      }
    });
    setSavedAgentId(resourceId);
    setSavedNotice("草稿已保存");
    setRollbackNotice(null);
  };

  const runTest = async () => {
    setRunning(true);
    setRunError(null);
    setRunOutput("");
    // H4：优先用本次保存生成的 agent id，其次编辑态 initialAgentId。
    const agentId = savedAgentId ?? initialAgentId ?? "assistant";
    try {
      await api.testRunAgent(agentId, { input: runInput }, (event) => {
        if (event.event === "token") {
          const data = event.data as { text?: string };
          setRunOutput((prev) => prev + (data.text ?? ""));
        }
        if (event.event === "error") {
          const data = event.data as { message?: string };
          setRunError(data.message ?? "unknown error");
        }
      });
    } finally {
      setRunning(false);
    }
  };

  const createModelInline = async () => {
    if (!newModelId.trim()) {
      return;
    }
    await api.createResource({
      resourceType: "model",
      resourceId: newModelId,
      version: "1",
      visibility: "private",
      spec: {
        plugin_type: "model_provider",
        protocol: "openai_compatible",
        base_url: "https://example.invalid/v1",
        model: newModelName || newModelId
      }
    });
    setModels((prev) => [...prev, { id: newModelId, label: newModelId }]);
    setModelId(newModelId);
    setModelCreateOpen(false);
  };

  return (
    <div className="page-stack">
      <PageHeader title="智能体工作台" description="以智能体为中心构建、预览与试跑。" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Card aria-label="智能体表单" title="构建">
          <div className="page-stack">
            <Input aria-label="智能体名" placeholder="智能体名" value={name} onChange={setName} />
            {errors.name ? <Typography.Text type="danger">{errors.name}</Typography.Text> : null}
            <Input aria-label="归属" placeholder="归属" value={owner} onChange={setOwner} />
            {errors.owner ? <Typography.Text type="danger">{errors.owner}</Typography.Text> : null}
            <Input
              aria-label="系统提示词"
              placeholder="系统提示词"
              value={systemPrompt}
              onChange={setSystemPrompt}
            />
            {errors.systemPrompt ? (
              <Typography.Text type="danger">{errors.systemPrompt}</Typography.Text>
            ) : null}
            <Input aria-label="补充指令" placeholder="补充指令（可选）" value={instructions} onChange={setInstructions} />

            <Space vertical align="start" style={{ width: "100%" }}>
              <Select
                aria-label="模型选择"
                style={{ width: "100%" }}
                value={modelId}
                optionList={models.map((m) => ({ label: m.label, value: m.id }))}
                onChange={(value) => setModelId(typeof value === "string" ? value : "")}
                placeholder="选择模型"
              />
              {modelCreateOpen ? (
                <div aria-label="内联新建模型" style={{ width: "100%" }}>
                  <Input aria-label="模型资源 ID" value={newModelId} onChange={setNewModelId} />
                  <Input aria-label="模型名" value={newModelName} onChange={setNewModelName} />
                  <Button aria-label="创建模型" onClick={() => void createModelInline()}>创建模型</Button>
                </div>
              ) : (
                <Button aria-label="新建模型" onClick={() => setModelCreateOpen(true)}>新建模型</Button>
              )}
            </Space>

            <Select
              aria-label="运行态选择"
              style={{ width: "100%" }}
              optionList={profiles.map((p) => ({ label: p.label, value: p.id }))}
              onChange={(value) => setRuntimeProfileId(typeof value === "string" ? value : "")}
              placeholder="运行态（可选，缺省同名）"
            />
            <CapabilityPicker api={api} selected={capabilities} onChange={setCapabilities} />
            <Input aria-label="记忆策略" placeholder="记忆策略引用（可选，Phase 2）" value={memoryPolicyRef} onChange={setMemoryPolicyRef} />
            <Input aria-label="个性化策略" placeholder="个性化策略引用（可选，Phase 2）" value={personalizationPolicyRef} onChange={setPersonalizationPolicyRef} />
            <Button theme="solid" onClick={() => void saveDraft()}>保存草稿</Button>
            {savedNotice ? <Typography.Text type="success">{savedNotice}</Typography.Text> : null}
          </div>
        </Card>

        <Card aria-label="智能体预览" title="预览">
          <Typography.Paragraph>{previewText || "（暂无内容）"}</Typography.Paragraph>
        </Card>
      </div>

      <Card aria-label="试跑" title="试跑">
        <Input aria-label="试跑输入" value={runInput} onChange={setRunInput} />
        <Space style={{ marginTop: 8 }}>
          <Button aria-label="试跑" disabled={running} onClick={() => void runTest()} theme="solid">
            试跑
          </Button>
          {runError ? (
            <Button aria-label="重试" disabled={running} onClick={() => void runTest()}>
              重试
            </Button>
          ) : null}
        </Space>
        {runError ? (
          <Typography.Text type="danger">试跑失败：{runError}</Typography.Text>
        ) : null}
        {/* TASK-017：试跑结果面板（流式输出落面板） */}
        <div
          aria-label="试跑结果面板"
          data-testid="test-run-output"
          style={{ marginTop: 8, whiteSpace: "pre-wrap" }}
        >
          {runOutput}
        </div>
      </Card>

      {/* TASK-017（C402）：能力资产引用展示——typed binding 可视化（type/ref/version 三元组） */}
      <Card title="能力资产引用">
        <CapabilityReferences capabilities={capabilities} />
      </Card>

      {/* TASK-017（C402）：版本管理（列表/对比/回滚入口） */}
      <Card title="版本管理">
        {rollbackNotice ? <Typography.Text type="success">{rollbackNotice}</Typography.Text> : null}
        <StudioVersionsPanel
          agentId={versionAgentId}
          error={versionsError}
          onCompare={setCompareTarget}
          onRetry={() => setVersionsReloadKey((key) => key + 1)}
          onRollback={(target) => void rollback(target)}
          versions={versions}
        />
      </Card>

      <Modal
        footer={
          <Button onClick={() => setCompareTarget(null)}>关闭</Button>
        }
        onCancel={() => setCompareTarget(null)}
        title="版本对比"
        visible={compareTarget !== null}
      >
        <div aria-label="版本对比内容" className="page-stack">
          <Descriptions row>
            <Descriptions.Item itemKey="版本">{compareTarget?.version}</Descriptions.Item>
            <Descriptions.Item itemKey="状态">{compareTarget?.status}</Descriptions.Item>
            <Descriptions.Item itemKey="更新时间">{compareTarget?.updatedAt}</Descriptions.Item>
          </Descriptions>
          <Typography.Text strong>版本 spec</Typography.Text>
          <pre aria-label="版本 spec JSON" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
            {compareTarget ? JSON.stringify(compareTarget.spec, null, 2) : ""}
          </pre>
        </div>
      </Modal>
    </div>
  );
}

/** TASK-017：能力资产引用（展示组件：props 只读）。 */
function CapabilityReferences({
  capabilities
}: {
  readonly capabilities: readonly CapabilitySelection[];
}) {
  if (capabilities.length === 0) {
    return <Empty description="未选择能力" />;
  }
  return (
    <div aria-label="能力资产引用" className="capability-references">
      {capabilities.map((item) => (
        <div className="capability-reference" key={`${item.type}:${item.capabilityRef}`}>
          <Tag color="violet">{item.type}</Tag>
          <Typography.Text code>{item.capabilityRef}</Typography.Text>
          <Typography.Text type="tertiary">v{item.versionPin}</Typography.Text>
        </div>
      ))}
    </div>
  );
}

/** TASK-017：版本管理面板（展示组件：列表 + 对比/回滚入口；四态）。 */
function StudioVersionsPanel(props: {
  readonly agentId: string | null;
  readonly versions: readonly ResourceVersion[] | null;
  readonly error: string | null;
  readonly onRetry: () => void;
  readonly onCompare: (version: ResourceVersion) => void;
  readonly onRollback: (version: ResourceVersion) => void;
}) {
  if (props.error !== null) {
    return (
      <Banner
        closeIcon={null}
        description={
          <span>
            {`加载失败：${props.error}`}
            <Button onClick={props.onRetry} size="small" style={{ marginLeft: 12 }}>
              重试
            </Button>
          </span>
        }
        type="danger"
      />
    );
  }
  if (props.agentId === null || props.versions === null || props.versions.length === 0) {
    return <Empty description="保存后展示版本" />;
  }
  const columns = [
    { dataIndex: "version", title: "版本" },
    {
      render: (_value: unknown, record: ResourceVersion) => <Tag>{record.status}</Tag>,
      title: "状态"
    },
    { dataIndex: "updatedAt", title: "更新时间" },
    {
      render: (_value: unknown, record: ResourceVersion) => (
        <Space>
          <Button onClick={() => props.onCompare(record)} size="small">
            对比
          </Button>
          <Button onClick={() => props.onRollback(record)} size="small">
            回滚到此版本
          </Button>
        </Space>
      ),
      title: "操作"
    }
  ];
  return (
    <div aria-label="Studio Versions">
      <Table columns={columns} dataSource={[...props.versions]} pagination={false} rowKey="version" />
    </div>
  );
}
