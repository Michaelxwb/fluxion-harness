import { Button, Input, InputNumber, Select, Space, Tabs, TextArea, Typography } from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceSummary } from "../../types/console";
import { AgentChannelsPanel } from "./AgentChannelsPanel";
import { AgentEvalPanel } from "./AgentEvalPanel";
import { AgentTestPanel } from "./AgentTestPanel";
import { AgentUsersPanel } from "./AgentUsersPanel";
import { CapabilityPicker } from "./CapabilityPicker";
import type { AgentEditorValue } from "./agentEditorModel";
import { referenceOptions } from "./agentEditorModel";

interface AgentEditorFormProps {
  readonly agentId: string;
  readonly api: ConsoleApi;
  readonly busy: boolean;
  readonly latestTraceId: string | null;
  readonly modelOptions: readonly ResourceSummary[];
  readonly notice: string | null;
  readonly profileOptions: readonly ResourceSummary[];
  readonly publishIssues: readonly string[] | null;
  readonly value: AgentEditorValue;
  readonly workflowOptions: readonly ResourceSummary[];
  readonly onChange: (change: Partial<AgentEditorValue>) => void;
  readonly onPublish: () => void;
  readonly onSave: () => void;
  readonly onTestCompleted: (traceId: string) => void;
}

/** TASK-012：Agent Editor 按产品职责分区；测试与评测留在 Agent 生命周期内。 */
export function AgentEditorForm(props: AgentEditorFormProps) {
  const { value, onChange } = props;
  return (
    <div style={{ display: "grid", rowGap: 16 }}>
      <Tabs defaultActiveKey="basic" keepDOM={false} type="line">
        <Tabs.TabPane itemKey="basic" tab="基本信息">
          <EditorSection>
            <TextField
              inputLabel="智能体名"
              label="名称 *"
              onChange={(name) => onChange({ name })}
              value={value.name}
            />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="prompt" tab="Prompt">
          <EditorSection>
            <TextField area label="系统提示词" onChange={(systemPrompt) => onChange({ systemPrompt })} value={value.systemPrompt} />
            <TextField area label="补充指令" onChange={(instructions) => onChange({ instructions })} value={value.instructions} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="model" tab="模型">
          <EditorSection>
            <ReferenceSelect
              label="主模型 *"
              labelId="agent-primary-model-label"
              onChange={(primaryModel) => onChange({ primaryModel })}
              options={referenceOptions(props.modelOptions)}
              value={value.primaryModel}
            />
            <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
              <NumberField label="模型调用超时（ms）" onChange={(modelTimeoutMs) => onChange({ modelTimeoutMs })} value={value.modelTimeoutMs} />
              <NumberField label="模型执行截止（ms）" onChange={(modelDeadlineMs) => onChange({ modelDeadlineMs })} value={value.modelDeadlineMs} />
            </div>
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="capabilities" tab="能力">
          <EditorSection>
            <Typography.Text>能力绑定</Typography.Text>
            <CapabilityPicker api={props.api} selected={value.capabilities} onChange={(capabilities) => onChange({ capabilities })} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="workflow" tab="工作流">
          <EditorSection>
            <ReferenceSelect
              label="默认工作流"
              labelId="agent-workflow-label"
              onChange={(workflow) => onChange({ workflow })}
              optional
              options={referenceOptions(props.workflowOptions)}
              value={value.workflow}
            />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="advanced" tab="高级设置">
          <EditorSection>
            <ReferenceSelect
              label="RuntimeProfile"
              labelId="agent-runtime-profile-label"
              onChange={(runtimeProfile) => onChange({ runtimeProfile })}
              optional
              options={referenceOptions(props.profileOptions)}
              value={value.runtimeProfile}
            />
            <TextField label="记忆策略引用" onChange={(memoryPolicy) => onChange({ memoryPolicy })} placeholder="resource-id@version" value={value.memoryPolicy} />
            <TextField label="个性化策略引用" onChange={(personalizationPolicy) => onChange({ personalizationPolicy })} placeholder="resource-id@version" value={value.personalizationPolicy} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="users" tab="用户">
          <EditorSection>
            <AgentUsersPanel agentId={props.agentId} api={props.api} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="channels" tab="渠道">
          <EditorSection>
            <AgentChannelsPanel agentId={props.agentId} api={props.api} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="test" tab="测试">
          <EditorSection>
            <AgentTestPanel agentId={props.agentId} api={props.api} onCompleted={props.onTestCompleted} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="eval" tab="评测">
          <EditorSection>
            <AgentEvalPanel agentId={props.agentId} api={props.api} latestTraceId={props.latestTraceId} />
          </EditorSection>
        </Tabs.TabPane>
        <Tabs.TabPane itemKey="versions" tab="版本">
          <EmptySection text="版本历史请从智能体详情查看" />
        </Tabs.TabPane>
      </Tabs>
      <Space>
        <Button loading={props.busy} onClick={props.onSave} theme="solid" type="primary">保存</Button>
        <Button loading={props.busy} onClick={props.onPublish} type="primary">发布</Button>
      </Space>
      {props.notice ? <Typography.Text type="success">{props.notice}</Typography.Text> : null}
      <PublishIssues issues={props.publishIssues} />
    </div>
  );
}

function EditorSection({ children }: { readonly children: React.ReactNode }) {
  return <div style={{ display: "grid", gap: 16, maxWidth: 720, paddingTop: 8 }}>{children}</div>;
}

function EmptySection({ text }: { readonly text: string }) {
  return <Typography.Text type="tertiary">{text}</Typography.Text>;
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
  readonly label: string;
  readonly labelId: string;
  readonly optional?: boolean;
  readonly options: readonly { readonly label: string; readonly value: string }[];
  readonly value: string;
  readonly onChange: (value: string) => void;
}) {
  const options = props.optional ? [{ label: "不设置", value: "" }, ...props.options] : [...props.options];
  return (
    <div>
      <Typography.Text id={props.labelId}>{props.label}</Typography.Text>
      <Select aria-labelledby={props.labelId} filter onChange={(next) => props.onChange(String(next ?? ""))} optionList={options} style={{ width: "100%" }} value={props.value} />
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
