import { useState } from "react";

import { Switch, TextArea, Typography } from "@douyinfe/semi-ui";

import { SchemaForm } from "./SchemaForm";
import { isJsonRecord } from "../utils/json";
import type { JsonRecord, JsonSchemaNode } from "../types/console";

/**
 * ADR-012：spec 编辑统一入口。
 * 默认用 SchemaForm 结构化编辑（用户无需手写 JSON）；「高级 JSON 模式」
 * 作为逃逸舱——schema 暂未覆盖或需要粘贴已有 JSON 时使用。JSON 模式下
 * 仅当文本可解析为 JSON Object 时才把结果回灌 spec，否则就地报错、不回灌。
 */
export interface SpecFormProps {
  readonly schema: JsonSchemaNode;
  readonly spec: JsonRecord;
  readonly onChange: (next: JsonRecord) => void;
  readonly disabled?: boolean;
  readonly jsonLabel?: string;
}

export function SpecForm({ schema, spec, onChange, disabled = false, jsonLabel = "规格 JSON" }: SpecFormProps) {
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);

  function enterJsonMode(): void {
    setJsonText(JSON.stringify(spec, null, 2));
    setJsonError(null);
    setJsonMode(true);
  }

  function exitJsonMode(): void {
    const parsed = tryParse(jsonText);
    if (parsed === undefined) return; // 解析失败：留在 JSON 模式，由 jsonError 提示
    onChange(parsed);
    setJsonMode(false);
  }

  function editJson(text: string): void {
    setJsonText(text);
    const parsed = tryParse(text);
    if (parsed === undefined) {
      setJsonError("规格必须是合法的 JSON Object");
      return;
    }
    setJsonError(null);
    onChange(parsed);
  }

  if (jsonMode) {
    return (
      <div className="page-stack">
        <label style={{ display: "flex", alignItems: "center", columnGap: 8 }}>
          <Switch checked onChange={exitJsonMode} disabled={disabled} aria-label="高级 JSON 模式" />
          <Typography.Text>高级 JSON 模式</Typography.Text>
        </label>
        <TextArea
          aria-label={jsonLabel}
          autosize={{ minRows: 10, maxRows: 24 }}
          className="json-input"
          disabled={disabled}
          onChange={editJson}
          value={jsonText}
        />
        {jsonError ? <Typography.Text type="danger">{jsonError}</Typography.Text> : null}
      </div>
    );
  }

  return (
    <div className="page-stack">
      <label style={{ display: "flex", alignItems: "center", columnGap: 8 }}>
        <Switch checked={false} onChange={enterJsonMode} disabled={disabled} aria-label="高级 JSON 模式" />
        <Typography.Text type="tertiary">高级 JSON 模式</Typography.Text>
      </label>
      <SchemaForm schema={schema} value={spec} onChange={onChange} disabled={disabled} />
    </div>
  );
}

/** 解析 JSON 文本为 JsonRecord；非对象或解析失败返回 undefined。 */
function tryParse(text: string): JsonRecord | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return undefined;
  }
  return isJsonRecord(parsed) ? parsed : undefined;
}
