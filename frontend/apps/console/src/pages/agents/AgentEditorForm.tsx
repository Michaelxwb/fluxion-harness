import { useState } from "react";

import { Button, Input, InputNumber, Nav, Select, Space, TextArea, Typography } from "@douyinfe/semi-ui";
import { useSearchParams } from "react-router-dom";

import type { ConsoleApi, ResourceType, ResourceVersion } from "../../types/console";
import { useRemoteResourceOptions } from "../../components/useRemoteResourceOptions";
import { VersionHistory } from "../../components/VersionHistory";
import { AgentChannelsPanel } from "./AgentChannelsPanel";
import { AgentEvalPanel } from "./AgentEvalPanel";
import { AgentTestPanel } from "./AgentTestPanel";
import { AgentUsersPanel } from "./AgentUsersPanel";
import { CapabilityPicker } from "./CapabilityPicker";
import type { AgentEditorValue } from "./agentEditorModel";

interface AgentEditorFormProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
  readonly latestTraceId: string | null;
  readonly notice: string | null;
  readonly publishIssues: readonly string[] | null;
  readonly value: AgentEditorValue;
  readonly onChange: (change: Partial<AgentEditorValue>) => void;
  readonly onTestCompleted: (traceId: string) => void;
  readonly onRollbackDone?: () => void;
}

const EDITOR_TAB_GROUPS: readonly {
  readonly key: string;
  readonly text: string;
  readonly items: readonly { readonly key: string; readonly text: string }[];
}[] = [
  {
    key: "group-basic",
    text: "基础",
    items: [
      { key: "basic", text: "基本信息" },
      { key: "prompt", text: "Prompt" },
      { key: "model", text: "模型" }
    ]
  },
  {
    key: "group-build",
    text: "编排",
    items: [
      { key: "capabilities", text: "能力" },
      { key: "workflow", text: "工作流" },
      { key: "advanced", text: "高级设置" }
    ]
  },
  {
    key: "group-channels",
    text: "用户渠道",
    items: [
      { key: "users", text: "用户" },
      { key: "channels", text: "渠道" }
    ]
  },
  {
    key: "group-govern",
    text: "验证治理",
    items: [
      { key: "test", text: "测试" },
      { key: "eval", text: "评测" },
      { key: "versions", text: "版本" }
    ]
  }
];

const EDITOR_TAB_KEYS = EDITOR_TAB_GROUPS.flatMap((group) => group.items.map((item) => item.key));

/** TASK-012：Agent Editor 按产品职责分区；测试与评测留在 Agent 生命周期内。 */
export function AgentEditorForm(props: AgentEditorFormProps) {
  const { value, onChange } = props;
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") ?? "basic";
  const activeTab = EDITOR_TAB_KEYS.includes(requestedTab) ? requestedTab : "basic";
  const [profileReload, setProfileReload] = useState(0);
  const [creatingProfile, setCreatingProfile] = useState(false);
  const [profileHint, setProfileHint] = useState<string | null>(null);

  async function createTenantDefaultProfile(): Promise<void> {
    setCreatingProfile(true);
    setProfileHint(null);
    try {
      // 租户默认运行配置：未给 Agent 指定 RuntimeProfile 时走 ADR-A010 默认链。
      // 同租户只允许一个 default=true（后端发布治理拒绝并存），已存在则提示选择。
      let created: ResourceVersion;
      try {
        created = await props.api.createResource({
          resourceType: "runtime_profile",
          resourceId: "tenant-default",
          visibility: "tenant",
          spec: {
            request_timeout_ms: 30000,
            max_retries: 1,
            default: true
          }
        });
      } catch (cause) {
        if (cause instanceof Error && /already exists|conflict/i.test(cause.message)) {
          setProfileHint("已存在 tenant-default，请在下拉框中搜索选择");
          setProfileReload((key) => key + 1);
          return;
        }
        throw cause;
      }
      await props.api.publishVersion(created);
      setProfileReload((key) => key + 1);
      onChange({ runtimeProfile: created.resourceId });
      setProfileHint("已创建并发布租户默认配置，已自动选中");
    } catch (cause) {
      setProfileHint(cause instanceof Error ? `创建失败：${cause.message}` : "创建失败");
    } finally {
      setCreatingProfile(false);
    }
  }

  return (
    <div style={{ display: "grid", rowGap: 16 }}>
      <div className="agent-editor">
        <Nav
          className="agent-editor__nav"
          defaultOpenKeys={EDITOR_TAB_GROUPS.map((group) => group.key)}
          items={EDITOR_TAB_GROUPS.map((group) => ({
            itemKey: group.key,
            text: group.text,
            items: group.items.map((item) => ({ itemKey: item.key, text: item.text }))
          }))}
          onSelect={(data) => setSearchParams({ tab: String(data.itemKey) }, { replace: true })}
          selectedKeys={[activeTab]}
        />
        <div className="agent-editor__content">
          {activeTab === "basic" ? (
            <EditorSection>
              <TextField
                inputLabel="智能体名"
                label="名称 *"
                onChange={(name) => onChange({ name })}
                value={value.name}
              />
            </EditorSection>
          ) : null}
          {activeTab === "prompt" ? (
            <EditorSection>
              <TextField area label="系统提示词" onChange={(systemPrompt) => onChange({ systemPrompt })} value={value.systemPrompt} />
              <TextField area label="补充指令" onChange={(instructions) => onChange({ instructions })} value={value.instructions} />
            </EditorSection>
          ) : null}
          {activeTab === "model" ? (
            <EditorSection>
              <ReferenceSelect
                api={props.api}
                kind="model_definition"
                label="主模型 *"
                labelId="agent-primary-model-label"
                onChange={(primaryModel) => onChange({ primaryModel })}
                value={value.primaryModel}
              />
              <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
                <NumberField label="模型调用超时（ms）" onChange={(modelTimeoutMs) => onChange({ modelTimeoutMs })} value={value.modelTimeoutMs} />
                <NumberField label="模型执行截止（ms）" onChange={(modelDeadlineMs) => onChange({ modelDeadlineMs })} value={value.modelDeadlineMs} />
              </div>
            </EditorSection>
          ) : null}
          {activeTab === "capabilities" ? (
            <EditorSection>
              <Typography.Text>能力绑定</Typography.Text>
              <CapabilityPicker api={props.api} selected={value.capabilities} onChange={(capabilities) => onChange({ capabilities })} />
            </EditorSection>
          ) : null}
          {activeTab === "workflow" ? (
            <EditorSection>
              <ReferenceSelect
                api={props.api}
                kind="workflow"
                label="默认工作流"
                labelId="agent-workflow-label"
                onChange={(workflow) => onChange({ workflow })}
                optional
                value={value.workflow}
              />
            </EditorSection>
          ) : null}
          {activeTab === "advanced" ? (
            <EditorSection>
              <ReferenceSelect
                api={props.api}
                kind="runtime_profile"
                label="RuntimeProfile"
                labelId="agent-runtime-profile-label"
                onChange={(runtimeProfile) => onChange({ runtimeProfile })}
                optional
                reloadSignal={profileReload}
                value={value.runtimeProfile}
              />
              <Space>
                <Button
                  disabled={creatingProfile}
                  loading={creatingProfile}
                  onClick={() => void createTenantDefaultProfile()}
                  size="small"
                >
                  创建租户默认配置
                </Button>
              </Space>
              {profileHint ? (
                <Typography.Text type="tertiary" size="small">
                  {profileHint}
                </Typography.Text>
              ) : null}
              <TextField label="记忆策略引用" onChange={(memoryPolicy) => onChange({ memoryPolicy })} placeholder="resource-id@version" value={value.memoryPolicy} />
              <TextField label="个性化策略引用" onChange={(personalizationPolicy) => onChange({ personalizationPolicy })} placeholder="resource-id@version" value={value.personalizationPolicy} />
            </EditorSection>
          ) : null}
          {activeTab === "users" ? (
            <EditorSection>
              <AgentUsersPanel agentId={props.agentId} api={props.api} />
            </EditorSection>
          ) : null}
          {activeTab === "channels" ? (
            <EditorSection>
              <AgentChannelsPanel agentId={props.agentId} api={props.api} />
            </EditorSection>
          ) : null}
          {activeTab === "test" ? (
            <EditorSection>
              <AgentTestPanel agentId={props.agentId} api={props.api} onCompleted={props.onTestCompleted} />
            </EditorSection>
          ) : null}
          {activeTab === "eval" ? (
            <EditorSection>
              <AgentEvalPanel agentId={props.agentId} api={props.api} latestTraceId={props.latestTraceId} />
            </EditorSection>
          ) : null}
          {activeTab === "versions" ? (
            <EditorSection wide>
              <VersionHistory
                api={props.api}
                enableRollback
                onRollbackDone={props.onRollbackDone}
                resourceId={props.agentId}
                resourceType="agent_definition"
              />
            </EditorSection>
          ) : null}
        </div>
      </div>
      {props.notice ? <Typography.Text type="success">{props.notice}</Typography.Text> : null}
      <PublishIssues issues={props.publishIssues} />
    </div>
  );
}

function EditorSection({ children, wide }: { readonly children: React.ReactNode; readonly wide?: boolean }) {
  return (
    <div style={{ display: "grid", gap: 16, maxWidth: wide ? undefined : 720, paddingTop: 8 }}>
      {children}
    </div>
  );
}

function TextField(props: {
  readonly area?: boolean;
  readonly inputLabel?: string;
  readonly label: string;
  readonly placeholder?: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}) {
  const Control = props.area ? TextArea : Input;
  return (
    <div>
      <Typography.Text>{props.label}</Typography.Text>
      <Control aria-label={props.inputLabel ?? props.label} onChange={(next) => props.onChange(String(next))} placeholder={props.placeholder} value={props.value} />
    </div>
  );
}

function NumberField(props: { readonly label: string; readonly value: number; readonly onChange: (value: number) => void }) {
  return (
    <div>
      <Typography.Text>{props.label}</Typography.Text>
      <InputNumber aria-label={props.label.replace("（ms）", "")} min={1} onChange={(next) => props.onChange(Number(next))} style={{ width: "100%" }} value={props.value} />
    </div>
  );
}

function ReferenceSelect(props: {
  readonly api: ConsoleApi;
  readonly kind: ResourceType;
  readonly label: string;
  readonly labelId: string;
  readonly optional?: boolean;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly reloadSignal?: number;
}) {
  // FEAT-03：引用选择远程搜索（大数据集不再静默只取 100 条）。
  const remote = useRemoteResourceOptions(props.api, [props.kind], true, props.reloadSignal ?? 0);
  const options = props.optional
    ? [{ label: "不设置", value: "" }, ...remote.options]
    : [...remote.options];
  return (
    <div>
      <Typography.Text id={props.labelId}>{props.label}</Typography.Text>
      <Select aria-labelledby={props.labelId} filter={false} loading={remote.loading} onChange={(next) => props.onChange(String(next ?? ""))} onSearch={remote.onSearch} optionList={options.map((item) => ({ label: item.label, value: item.value }))} outerBottomSlot={remote.truncated ? (<Typography.Text type="tertiary" size="small">仅显示前 {remote.options.length} 条匹配，请细化关键词</Typography.Text>) : undefined} remote style={{ width: "100%" }} value={props.value} />
    </div>
  );
}

function PublishIssues({ issues }: { readonly issues: readonly string[] | null }) {
  if (!issues?.length) return null;
  return (
    <div aria-label="发布校验问题">
      <Typography.Text type="danger">无法发布，发现 {issues.length} 个问题：</Typography.Text>
      <ul>{issues.map((issue) => <li key={issue}><Typography.Text type="danger">{issue}</Typography.Text></li>)}</ul>
    </div>
  );
}
