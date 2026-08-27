import { useEffect, useMemo, useState } from "react";

import { Button, Card, Input, Select, Space, Table, Typography } from "@douyinfe/semi-ui";

import { PageHeader } from "../../components/PageHeader";
import type { ConsoleApi } from "../../types/console";

interface AgentStudioPageProps {
  readonly api: ConsoleApi;
  /** 编辑场景：直接载入该 agent（Phase 1 以新建为主，保留扩展位）。 */
  readonly initialAgentId?: string;
}

/** TASK-014 产出的能力选择器（Studio 内嵌版）：三类多选。 */
export function CapabilityPicker({
  api,
  selected,
  onChange
}: {
  readonly api: ConsoleApi;
  readonly selected: readonly string[];
  readonly onChange: (next: readonly string[]) => void;
}) {
  const [options, setOptions] = useState<readonly { id: string; kind: string }[]>([]);
  useEffect(() => {
    void (async () => {
      const merged: { id: string; kind: string }[] = [];
      for (const kind of ["skill", "tool", "mcp"] as const) {
        const items = await api.listVisibleResources(kind);
        merged.push(...items.map((i) => ({ id: i.resourceId, kind })));
      }
      setOptions(merged);
    })();
  }, [api]);

  const toggle = (id: string) => {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  };

  return (
    <div aria-label="能力绑定选择">
      {options.map((option) => (
        <label key={option.id} style={{ display: "block" }}>
          <input
            type="checkbox"
            checked={selected.includes(option.id)}
            onChange={() => toggle(option.id)}
          />
          {`${option.kind}:${option.id}`}
        </label>
      ))}
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
  const [capabilities, setCapabilities] = useState<readonly string[]>([]);
  const [memoryPolicyRef, setMemoryPolicyRef] = useState("");
  const [personalizationPolicyRef, setPersonalizationPolicyRef] = useState("");

  useEffect(() => {
    void api.listVisibleResources("model").then((items) => {
      setModels(items.map((m) => ({ id: m.resourceId, label: m.displayName || m.resourceId })));
    });
    void api.listVisibleResources("runtime_profile").then((items) => {
      setProfiles(items.map((p) => ({ id: p.resourceId, label: p.displayName || p.resourceId })));
    });
  }, [api]);

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
        model_ref: { id: modelId || "dev.echo", version: "1" }
      }
    });
    setSavedAgentId(resourceId);
    setSavedNotice("草稿已保存");
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
        <div data-testid="test-run-output" style={{ marginTop: 8 }}>
          {runOutput}
        </div>
      </Card>

      <Card title="能力绑定">
        <Typography.Text type="tertiary">
          能力绑定由「构建 → 能力」页统一管理；本页仅展示已绑定数量。
        </Typography.Text>
      </Card>
    </div>
  );
}
