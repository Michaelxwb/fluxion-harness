import {
  Button,
  Input,
  InputNumber,
  Select,
  Space,
  TextArea,
  Typography
} from "@douyinfe/semi-ui";

import type { ConsoleApi, ResourceSummary } from "../../types/console";
import { CapabilityPicker } from "./CapabilityPicker";
import type { AgentEditorValue } from "./agentEditorModel";
import { referenceOptions } from "./agentEditorModel";

interface AgentEditorFormProps {
  readonly api: ConsoleApi;
  readonly busy: boolean;
  readonly modelOptions: readonly ResourceSummary[];
  readonly notice: string | null;
  readonly profileOptions: readonly ResourceSummary[];
  readonly publishIssues: readonly string[] | null;
  readonly value: AgentEditorValue;
  readonly workflowOptions: readonly ResourceSummary[];
  readonly onChange: (change: Partial<AgentEditorValue>) => void;
  readonly onPublish: () => void;
  readonly onSave: () => void;
}

export function AgentEditorForm(props: AgentEditorFormProps) {
  const { value, onChange } = props;
  return (
    <div style={{ display: "grid", rowGap: 16, maxWidth: 720 }}>
      <TextField
        label="名称 *"
        inputLabel="智能体名"
        value={value.name}
        onChange={(name) => onChange({ name })}
      />
      <TextField
        area
        label="系统提示词"
        value={value.systemPrompt}
        onChange={(systemPrompt) => onChange({ systemPrompt })}
      />
      <TextField
        area
        label="补充指令"
        value={value.instructions}
        onChange={(instructions) => onChange({ instructions })}
      />
      <ReferenceSelect
        label="主模型 *"
        labelId="agent-primary-model-label"
        options={referenceOptions(props.modelOptions)}
        value={value.primaryModel}
        onChange={(primaryModel) => onChange({ primaryModel })}
      />
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <NumberField
          label="模型调用超时（ms）"
          value={value.modelTimeoutMs}
          onChange={(modelTimeoutMs) => onChange({ modelTimeoutMs })}
        />
        <NumberField
          label="模型执行截止（ms）"
          value={value.modelDeadlineMs}
          onChange={(modelDeadlineMs) => onChange({ modelDeadlineMs })}
        />
      </div>
      <ReferenceSelect
        optional
        label="RuntimeProfile"
        labelId="agent-runtime-profile-label"
        options={referenceOptions(props.profileOptions)}
        value={value.runtimeProfile}
        onChange={(runtimeProfile) => onChange({ runtimeProfile })}
      />
      <ReferenceSelect
        optional
        label="默认工作流"
        labelId="agent-workflow-label"
        options={referenceOptions(props.workflowOptions)}
        value={value.workflow}
        onChange={(workflow) => onChange({ workflow })}
      />
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <TextField
          label="记忆策略引用"
          placeholder="resource-id@version"
          value={value.memoryPolicy}
          onChange={(memoryPolicy) => onChange({ memoryPolicy })}
        />
        <TextField
          label="个性化策略引用"
          placeholder="resource-id@version"
          value={value.personalizationPolicy}
          onChange={(personalizationPolicy) => onChange({ personalizationPolicy })}
        />
      </div>
      <div>
        <Typography.Text>能力绑定</Typography.Text>
        <CapabilityPicker
          api={props.api}
          selected={value.capabilities}
          onChange={(capabilities) => onChange({ capabilities })}
        />
      </div>
      <Space>
        <Button loading={props.busy} onClick={props.onSave} theme="solid" type="primary">保存</Button>
        <Button loading={props.busy} onClick={props.onPublish} type="primary">发布</Button>
      </Space>
      {props.notice ? <Typography.Text type="success">{props.notice}</Typography.Text> : null}
      <PublishIssues issues={props.publishIssues} />
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
      <Control
        aria-label={props.inputLabel ?? props.label}
        onChange={(next) => props.onChange(String(next))}
        placeholder={props.placeholder}
        value={props.value}
      />
    </div>
  );
}

function NumberField(props: { readonly label: string; readonly value: number; readonly onChange: (value: number) => void }) {
  return (
    <div>
      <Typography.Text>{props.label}</Typography.Text>
      <InputNumber
        aria-label={props.label.replace("（ms）", "")}
        min={1}
        onChange={(next) => props.onChange(Number(next))}
        style={{ width: "100%" }}
        value={props.value}
      />
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
      <Select
        aria-labelledby={props.labelId}
        filter
        onChange={(next) => props.onChange(String(next ?? ""))}
        optionList={options}
        style={{ width: "100%" }}
        value={props.value}
      />
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
