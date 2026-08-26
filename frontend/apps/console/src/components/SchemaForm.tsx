import {
  Button,
  Input,
  InputNumber,
  Select,
  Switch,
  TextArea,
  Typography
} from "@douyinfe/semi-ui";
import { IconDelete, IconPlus } from "@douyinfe/semi-icons";

import type { JsonRecord, JsonSchemaNode, JsonValue } from "../types/console";

/**
 * ADR-012：由后端 spec model 的 JSON Schema 自渲染的结构化表单。
 * 用户不再手写 JSON：string→Input/TextArea、integer→InputNumber、
 * enum→Select、boolean→Switch、array→动态增删行、结构化 object→边框分组、
 * dict[str,str]→键值对编辑器；description 作为字段说明展示。
 * 清空可选字段即从 spec 中移除该键（不提交空串/空数组）。
 */

/** 惯例上按多行文本编辑的字段（JSON Schema 未区分长文本，按字段名约定）。 */
const LONG_TEXT_FIELDS = new Set(["prompt", "instructions", "description", "expected"]);

export interface SchemaFormProps {
  readonly schema: JsonSchemaNode;
  readonly value: JsonRecord;
  readonly onChange: (next: JsonRecord) => void;
  readonly disabled?: boolean;
}

export function SchemaForm({ schema, value, onChange, disabled = false }: SchemaFormProps) {
  return (
    <ObjectFields node={schema} root={schema} value={value} onChange={onChange} disabled={disabled} depth={0} />
  );
}

/** 按 schema 默认值构造初始 spec：必填原始类型留空串待用户填写，可选空值不落键。 */
export function specFromSchema(schema: JsonSchemaNode, root: JsonSchemaNode = schema): JsonRecord {
  const resolved = resolveNode(schema, root);
  const record: Record<string, JsonValue> = {};
  for (const [key, property] of Object.entries(resolved.properties ?? {})) {
    const node = resolveNode(property, root);
    const kind = widgetKind(node);
    if (kind === "null") continue;
    // 单值 Literal 在 schema 里是 const：预填固定值，用户无需手输。
    if (node.const !== undefined) {
      record[key] = node.const;
      continue;
    }
    if (node.default !== undefined && node.default !== null) {
      record[key] = node.default;
      continue;
    }
    if (kind === "object") {
      record[key] = specFromSchema(node, root);
      continue;
    }
    // 无显式默认的可选字段不落键：后端 model 缺省即空（absence == default）
    if (!isRequired(resolved, key)) continue;
    // 必填字段：数组初始化为空数组（保持类型正确），其余原始类型留空串待填写。
    record[key] = kind === "array" ? [] : "";
  }
  return record;
}

interface ObjectFieldsProps {
  readonly node: JsonSchemaNode;
  readonly root: JsonSchemaNode;
  readonly value: JsonRecord;
  readonly onChange: (next: JsonRecord) => void;
  readonly disabled: boolean;
  readonly depth: number;
}

function ObjectFields({ node, root, value, onChange, disabled, depth }: ObjectFieldsProps) {
  const resolved = resolveNode(node, root);
  const fields = Object.entries(resolved.properties ?? {});
  // 顶层单列保线性语义顺序与 tab 顺序一致；嵌套对象（如 model_policy）改双栏，
  // 短字段并排、长文本/数组/对象/键值对整行，压缩纵向高度。
  const twoColumn = depth >= 1;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: twoColumn ? "1fr 1fr" : "1fr",
        columnGap: 16,
        rowGap: 12
      }}
    >
      {fields.map(([key, property]) => (
        <SchemaField
          key={key}
          fieldKey={key}
          node={property}
          root={root}
          parent={value}
          onParentChange={onChange}
          required={isRequired(resolved, key)}
          disabled={disabled}
          depth={depth}
        />
      ))}
    </div>
  );
}

interface SchemaFieldProps {
  readonly fieldKey: string;
  readonly node: JsonSchemaNode;
  readonly root: JsonSchemaNode;
  readonly parent: JsonRecord;
  readonly onParentChange: (next: JsonRecord) => void;
  readonly required: boolean;
  readonly disabled: boolean;
  readonly depth: number;
}

function SchemaField({
  fieldKey,
  node,
  root,
  parent,
  onParentChange,
  required,
  disabled,
  depth
}: SchemaFieldProps) {
  const resolved = resolveNode(node, root);
  const label = resolved.title ?? fieldKey;
  const current = parent[fieldKey];
  const setValue = (next: JsonValue | undefined) => {
    if (next === undefined) {
      const copy: Record<string, JsonValue> = { ...parent };
      delete copy[fieldKey];
      onParentChange(copy);
      return;
    }
    onParentChange({ ...parent, [fieldKey]: next });
  };
  const kind = widgetKind(resolved);

  // 顶层直出的结构化对象直接平铺；深层嵌套以边框分组呈现，层级一目了然。
  if (kind === "object" && depth === 0) {
    return (
      <ObjectFields
        node={resolved}
        root={root}
        value={isRecord(current) ? current : {}}
        onChange={setValue}
        disabled={disabled}
        depth={1}
      />
    );
  }

  if (kind === "object") {
    return (
      <fieldset
        style={{
          border: "1px solid var(--semi-color-border)",
          borderRadius: 6,
          padding: "8px 12px",
          display: "grid",
          rowGap: 12,
          gridColumn: "1 / -1"
        }}
      >
        <legend>
          <Typography.Text strong>{label}</Typography.Text>
        </legend>
        <ObjectFields
          node={resolved}
          root={root}
          value={isRecord(current) ? current : {}}
          onChange={setValue}
          disabled={disabled}
          depth={depth + 1}
        />
        {resolved.description ? (
          <Typography.Text type="tertiary" size="small">
            {resolved.description}
          </Typography.Text>
        ) : null}
      </fieldset>
    );
  }

  return (
    <div style={{ display: "grid", rowGap: 4, gridColumn: isFullWidthField(fieldKey, kind) ? "1 / -1" : undefined }}>
      <label>
        {required ? <span aria-hidden="true" style={{ color: "var(--semi-color-danger)" }}>*</span> : null}
        <Typography.Text strong>{label}</Typography.Text>
      </label>
      {renderWidget({ kind, node: resolved, root, value: current, onChange: setValue, disabled, fieldKey, label, depth })}
      {resolved.description ? (
        <Typography.Text type="tertiary" size="small">
          {resolved.description}
        </Typography.Text>
      ) : null}
    </div>
  );
}

interface WidgetProps {
  readonly kind: WidgetKind;
  readonly node: JsonSchemaNode;
  readonly root: JsonSchemaNode;
  readonly value: JsonValue | undefined;
  readonly onChange: (next: JsonValue | undefined) => void;
  readonly disabled: boolean;
  readonly fieldKey: string;
  readonly label: string;
  readonly depth: number;
}

function renderWidget({ kind, node, root, value, onChange, disabled, fieldKey, label, depth }: WidgetProps): React.ReactNode {
  if (kind === "enum") {
    const options = node.enum ?? (node.const !== undefined ? [node.const] : []);
    return (
      <Select
        aria-label={label}
        placeholder="请选择"
        value={value === undefined || value === null ? undefined : (value as string | number)}
        optionList={options.map((item) => ({
          value: typeof item === "number" ? item : String(item),
          label: String(item)
        }))}
        onChange={(next) => onChange(next === "" ? undefined : (next as JsonValue))}
        disabled={disabled}
        style={{ width: "100%" }}
      />
    );
  }
  if (kind === "string") {
    const text = typeof value === "string" ? value : "";
    const shared = {
      "aria-label": label,
      value: text,
      onChange: (next: string) => onChange(next === "" ? undefined : next),
      disabled,
      placeholder: node.description ?? ""
    };
    return LONG_TEXT_FIELDS.has(fieldKey) ? <TextArea {...shared} rows={4} /> : <Input {...shared} />;
  }
  if (kind === "integer" || kind === "number") {
    return (
      <InputNumber
        aria-label={label}
        value={typeof value === "number" ? value : undefined}
        onChange={(next) => onChange(next === "" || next === null ? undefined : (next as number))}
        disabled={disabled}
        style={{ width: "100%" }}
      />
    );
  }
  if (kind === "boolean") {
    return <Switch aria-label={label} checked={value === true} onChange={(next) => onChange(next)} disabled={disabled} />;
  }
  if (kind === "array") {
    return <ArrayField node={node} root={root} value={value} onChange={onChange} disabled={disabled} fieldKey={fieldKey} label={label} depth={depth} />;
  }
  if (kind === "map") {
    return <KeyValueField value={value} onChange={onChange} disabled={disabled} label={label} />;
  }
  return <Typography.Text type="tertiary">暂不支持的字段类型</Typography.Text>;
}

interface ArrayFieldProps {
  readonly node: JsonSchemaNode;
  readonly root: JsonSchemaNode;
  readonly value: JsonValue | undefined;
  readonly onChange: (next: JsonValue | undefined) => void;
  readonly disabled: boolean;
  readonly fieldKey: string;
  readonly label: string;
  readonly depth: number;
}

function ArrayField({ node, root, value, onChange, disabled, fieldKey, label, depth }: ArrayFieldProps) {
  const items = resolveNode(node.items ?? { type: "string" }, root);
  const kind = widgetKind(items);
  const entries = Array.isArray(value) ? value : [];
  const emit = (next: readonly JsonValue[]) => onChange(next.length === 0 ? undefined : [...next]);

  const emptyItem = (): JsonValue => {
    if (kind === "object") return {};
    if (kind === "integer" || kind === "number") return 0;
    if (kind === "boolean") return false;
    return "";
  };

  return (
    <div style={{ display: "grid", rowGap: 8 }}>
      {entries.map((item, index) => (
        <div key={index} style={{ display: "flex", columnGap: 8, alignItems: "flex-start" }}>
          <div style={{ flex: 1 }}>
            {kind === "object" ? (
              <ObjectFields
                node={items}
                root={root}
                value={isRecord(item) ? item : {}}
                onChange={(next) => {
                  const copy = [...entries];
                  copy[index] = next;
                  emit(copy);
                }}
                disabled={disabled}
                depth={depth + 1}
              />
            ) : (
              renderWidget({
                kind,
                node: items,
                root,
                value: item,
                onChange: (next) => {
                  if (next === undefined) return;
                  const copy = [...entries];
                  copy[index] = next;
                  emit(copy);
                },
                disabled,
                fieldKey,
                label,
                depth
              })
            )}
          </div>
          <Button
            icon={<IconDelete />}
            theme="borderless"
            type="danger"
            aria-label={`删除 ${label} 第 ${index + 1} 项`}
            disabled={disabled}
            onClick={() => emit(entries.filter((_, position) => position !== index))}
          />
        </div>
      ))}
      <Button
        icon={<IconPlus />}
        theme="light"
        disabled={disabled}
        aria-label={`添加 ${label}`}
        onClick={() => emit([...entries, emptyItem()])}
      >
        添加
      </Button>
    </div>
  );
}

interface KeyValueFieldProps {
  readonly value: JsonValue | undefined;
  readonly onChange: (next: JsonValue | undefined) => void;
  readonly disabled: boolean;
  readonly label: string;
}

function KeyValueField({ value, onChange, disabled, label }: KeyValueFieldProps) {
  // dict[str, str]（env/headers 等自由键值对象）：键值成对编辑，空键在提交前由用户补全。
  const record = isRecord(value) ? value : {};
  const emit = (next: Record<string, string>) =>
    onChange(Object.keys(next).length === 0 ? undefined : (next as unknown as JsonRecord));

  return (
    <div style={{ display: "grid", rowGap: 8 }}>
      {Object.keys(record).map((key) => (
        <div key={key} style={{ display: "flex", columnGap: 8 }}>
          <Input
            value={key}
            disabled={disabled}
            aria-label={`${fieldLabel(key)} 键`}
            onChange={(nextKey) => {
              if (nextKey === "" || nextKey === key) return;
              const copy: Record<string, string> = {};
              for (const [existing, item] of Object.entries(record)) {
                copy[existing === key ? nextKey : existing] = String(item);
              }
              emit(copy);
            }}
          />
          <Input
            value={String(record[key] ?? "")}
            disabled={disabled}
            aria-label={`${fieldLabel(key)} 值`}
            style={{ flex: 1 }}
            onChange={(nextValue) => emit({ ...stringRecord(record), [key]: nextValue })}
          />
          <Button
            icon={<IconDelete />}
            theme="borderless"
            type="danger"
            aria-label={`删除 ${fieldLabel(key)}`}
            disabled={disabled}
            onClick={() => {
              const copy = stringRecord(record);
              delete copy[key];
              emit(copy);
            }}
          />
        </div>
      ))}
      <Button
        icon={<IconPlus />}
        theme="light"
        disabled={disabled}
        aria-label={`添加 ${label}`}
        onClick={() => emit({ ...stringRecord(record), "": "" })}
      >
        添加
      </Button>
    </div>
  );
}

function fieldLabel(key: string): string {
  return key === "" ? "新键" : key;
}

function stringRecord(value: JsonRecord): Record<string, string> {
  const copy: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) copy[key] = String(item);
  return copy;
}

/** 解析 $ref（#/$defs/Name，pydantic 嵌套模型的引用形式）与 anyOf（pydantic 对
 * Optional 字段的输出形式 anyOf:[{type:...},{type:"null"}]）；兄弟键（title/
 * description/default）覆盖目标。anyOf 取首个非 null 子模式，使 Optional 字段
 * 能落到正确 widget，而非退化成「暂不支持的字段类型」。 */
function resolveNode(node: JsonSchemaNode, root: JsonSchemaNode): JsonSchemaNode {
  const ref = node.$ref;
  let resolved = node;
  if (ref) {
    const name = ref.startsWith("#/$defs/") ? ref.slice("#/$defs/".length) : "";
    const target = root.$defs?.[name];
    if (target) {
      const { $ref: _dropped, ...siblings } = node;
      void _dropped;
      resolved = { ...target, ...siblings };
    }
  }
  const anyOf = resolved.anyOf;
  if (anyOf?.length) {
    const primary = anyOf.find((sub) => sub.type !== "null") ?? anyOf[0];
    const { anyOf: _dropped, ...siblings } = resolved;
    void _dropped;
    resolved = { ...primary, ...siblings };
  }
  return resolved;
}

type WidgetKind =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "enum"
  | "array"
  | "object"
  | "map"
  | "null";

function widgetKind(node: JsonSchemaNode): WidgetKind {
  // const（单值 Literal）按枚举处理：渲染为单选项下拉，避免用户手输固定值。
  if (node.enum?.length || node.const !== undefined) return "enum";
  const types = Array.isArray(node.type) ? node.type : node.type ? [node.type] : [];
  const primary = types.find((entry) => entry !== "null");
  if (primary === "string") return "string";
  if (primary === "integer") return "integer";
  if (primary === "number") return "number";
  if (primary === "boolean") return "boolean";
  if (primary === "array") return "array";
  if (primary === "object") {
    // 有 properties 的是结构化对象；只有 additionalProperties 的是自由键值对
    return node.properties && Object.keys(node.properties).length > 0 ? "object" : "map";
  }
  return "null";
}

/** 双栏排版下整行占位的字段：结构化对象 / 数组 / 键值对 / 多行文本。 */
function isFullWidthField(fieldKey: string, kind: WidgetKind): boolean {
  if (kind === "object" || kind === "array" || kind === "map") return true;
  return LONG_TEXT_FIELDS.has(fieldKey);
}

function isRequired(node: JsonSchemaNode, key: string): boolean {
  return (node.required ?? []).includes(key);
}

function isRecord(value: JsonValue | undefined): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
