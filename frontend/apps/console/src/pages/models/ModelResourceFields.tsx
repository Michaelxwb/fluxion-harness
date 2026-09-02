import type { ReactNode } from "react";

import { Input, InputNumber, Select, Space, Switch, Typography } from "@douyinfe/semi-ui";

import type { JsonRecord } from "../../types/console";

interface EditorFieldsProps {
  readonly spec: JsonRecord;
  readonly onChange: (spec: JsonRecord) => void;
}

export function ProviderFields({ spec, onChange }: EditorFieldsProps) {
  return (
    <>
      <Field label="协议">
        <Select
          aria-label="协议"
          disabled
          optionList={[{ label: "OpenAI Compatible", value: "openai-compatible" }]}
          style={{ width: "100%" }}
          value={String(spec.protocol ?? "openai-compatible")}
        />
      </Field>
      <Field label="Base URL">
        <Input
          aria-label="Base URL"
          onChange={(value) => onChange({ ...spec, base_url: value })}
          value={String(spec.base_url ?? "")}
        />
      </Field>
      <Field label="凭据引用">
        <Input
          aria-label="凭据引用"
          onChange={(value) => onChange({ ...spec, credential_ref: value })}
          value={String(spec.credential_ref ?? "")}
        />
      </Field>
      <Field label="默认模型">
        <Input
          aria-label="默认模型"
          onChange={(value) => onChange({ ...spec, default_model: value || null })}
          value={String(spec.default_model ?? "")}
        />
      </Field>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <NumberField
          label="请求超时（ms）"
          onChange={(value) => onChange({ ...spec, request_timeout_ms: value })}
          value={Number(spec.request_timeout_ms ?? 60_000)}
        />
        <NumberField
          label="重试次数"
          min={0}
          onChange={(value) => onChange({ ...spec, max_retries: value })}
          value={Number(spec.max_retries ?? 1)}
        />
      </div>
    </>
  );
}

export function ModelFields({
  spec,
  providerOptions,
  onChange
}: EditorFieldsProps & {
  readonly providerOptions: readonly { readonly label: string; readonly value: string }[];
}) {
  const capabilities = recordFrom(spec.capabilities);
  const updateCapability = (key: string, value: number | boolean) =>
    onChange({ ...spec, capabilities: { ...capabilities, [key]: value } });
  return (
    <>
      <Field label="模型名">
        <Input
          aria-label="模型名"
          onChange={(value) => onChange({ ...spec, name: value })}
          value={String(spec.name ?? "")}
        />
      </Field>
      <Field label="模型服务" labelId="model-provider-label">
        <Select
          aria-labelledby="model-provider-label"
          filter
          onChange={(value) => onChange({ ...spec, provider_ref: parseRef(String(value)) })}
          optionList={[...providerOptions]}
          style={{ width: "100%" }}
          value={formatRef(recordFrom(spec.provider_ref))}
        />
      </Field>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "1fr 1fr" }}>
        <NumberField
          label="上下文窗口"
          onChange={(value) => updateCapability("context_window", value)}
          value={Number(capabilities.context_window ?? 1)}
        />
        <NumberField
          label="最大输出 Token"
          onChange={(value) => updateCapability("max_tokens", value)}
          value={Number(capabilities.max_tokens ?? 1)}
        />
      </div>
      <Space spacing="loose">
        <Switch
          aria-label="支持工具调用"
          checked={Boolean(capabilities.tool_calling)}
          onChange={(checked) => updateCapability("tool_calling", checked)}
        />
        <Typography.Text>工具调用</Typography.Text>
        <Switch
          aria-label="支持视觉"
          checked={Boolean(capabilities.vision)}
          onChange={(checked) => updateCapability("vision", checked)}
        />
        <Typography.Text>视觉</Typography.Text>
      </Space>
    </>
  );
}

function NumberField({
  label,
  min = 1,
  value,
  onChange
}: {
  readonly label: string;
  readonly min?: number;
  readonly value: number;
  readonly onChange: (value: number) => void;
}) {
  return (
    <Field label={label}>
      <InputNumber
        aria-label={label}
        min={min}
        onChange={(next) => onChange(Number(next))}
        style={{ width: "100%" }}
        value={value}
      />
    </Field>
  );
}

function Field({
  children,
  label,
  labelId
}: {
  readonly children: ReactNode;
  readonly label: string;
  readonly labelId?: string;
}) {
  return <div><Typography.Text id={labelId}>{label}</Typography.Text>{children}</div>;
}

function recordFrom(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function formatRef(value: JsonRecord): string {
  return value.id && value.version ? `${String(value.id)}@${String(value.version)}` : "";
}

function parseRef(value: string): JsonRecord {
  const index = value.lastIndexOf("@");
  return { id: value.slice(0, index), version: value.slice(index + 1) };
}
