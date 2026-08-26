import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { SchemaForm, specFromSchema } from "../SchemaForm";
import { IN_MEMORY_RESOURCE_SCHEMAS } from "../../services/inMemorySchemas";
import type { JsonRecord, JsonSchemaNode } from "../../types/console";

const runtimeProfileSchema = IN_MEMORY_RESOURCE_SCHEMAS.runtime_profile;

interface Harness {
  readonly changes: JsonRecord[];
}

/** SchemaForm 是受控组件：页面用法需把 onChange 回灌进 value，测试同样如此。 */
function renderForm(schema: JsonSchemaNode): Harness {
  const changes: JsonRecord[] = [];
  function Controlled() {
    const [value, setValue] = useState<JsonRecord>(() => specFromSchema(schema));
    return (
      <SchemaForm
        schema={schema}
        value={value}
        onChange={(next) => {
          changes.push(next);
          setValue(next);
        }}
      />
    );
  }
  render(<Controlled />);
  return { changes };
}

describe("RS7 specFromSchema", () => {
  it("按 schema 默认值预填，必填字符串留空，无默认的数组/可选字段不落键", () => {
    const spec = specFromSchema(runtimeProfileSchema);
    expect(spec.prompt).toBe("");
    expect(spec.model_policy).toEqual({
      timeout_ms: 60000,
      deadline_ms: 120000,
      max_rounds: 8
    });
    expect("allowed_skills" in spec).toBe(false);
    expect("guardrail_policy" in spec).toBe(false);
    expect("display_name" in spec).toBe(false);
  });

  it("默认值直接落在顶层（plugin 的 request_timeout_ms/max_retries）", () => {
    const spec = specFromSchema(IN_MEMORY_RESOURCE_SCHEMAS.plugin);
    expect(spec.request_timeout_ms).toBe(60000);
    expect(spec.max_retries).toBe(1);
  });
});

describe("RS7 SchemaForm 渲染", () => {
  it("必填字段带星号标记，description 作为说明展示", () => {
    renderForm(runtimeProfileSchema);
    expect(screen.getByText("*")).toBeInTheDocument();
    expect(screen.getByText("系统提示词")).toBeInTheDocument();
    expect(screen.getByText("System Prompt：助手的人格与行为准则")).toBeInTheDocument();
  });

  it("$ref 嵌套对象（model_policy）的字段可编辑", async () => {
    const user = userEvent.setup();
    const { changes } = renderForm(runtimeProfileSchema);

    const timeout = screen.getByDisplayValue("60000");
    await user.clear(timeout);
    await user.type(timeout, "30000");

    expect(changes.at(-1)?.model_policy).toMatchObject({ timeout_ms: 30000, deadline_ms: 120000 });
  });

  it("枚举字段渲染为下拉选择并回写 spec", async () => {
    const user = userEvent.setup();
    const { changes } = renderForm(IN_MEMORY_RESOURCE_SCHEMAS.mcp);

    await user.click(screen.getByRole("combobox"));
    const options = await waitFor(() => screen.getAllByRole("option"));
    expect(options[0].textContent).toContain("stdio");
    fireEvent.click(options[0]);
    // Semi 受控 Select 的 onChange 在下拉关闭动画的 afterClose 回调里触发
    //（semi-foundation select _handleSingleSelect → close({closeCb})）。
    // jsdom 不派发 CSS animationend，关不到 → closeCb 永不执行；这里补发
    // 一次 animationend 模拟动画结束，令受控值回写得以 flush。
    const leaving = document.querySelector('[class*="animation-hide"]');
    if (leaving) fireEvent.animationEnd(leaving);

    await waitFor(() => expect(changes.at(-1)?.transport).toBe("stdio"));
  });

  it("数组字段动态增删，清空后整键移除", async () => {
    const user = userEvent.setup();
    const { changes } = renderForm(IN_MEMORY_RESOURCE_SCHEMAS.policy);

    await user.type(screen.getByPlaceholderText("策略名（展示用）"), "tenant-a-policy");
    await user.click(screen.getByRole("button", { name: "添加 工具白名单" }));
    await user.type(screen.getByDisplayValue(""), "mcp__weather__current");

    expect(changes.at(-1)?.allowed_tools).toEqual(["mcp__weather__current"]);

    await user.click(screen.getByRole("button", { name: "删除 工具白名单 第 1 项" }));
    expect("allowed_tools" in (changes.at(-1) ?? {})).toBe(false);
  });

  it("anyOf（Optional）字段渲染为输入框并可编辑", async () => {
    // pydantic 对 Optional 字段输出 anyOf:[{type:string},{type:null}]；
    // resolveNode 须取首个非 null 子模式，否则退化成「暂不支持的字段类型」。
    const user = userEvent.setup();
    const { changes } = renderForm(runtimeProfileSchema);
    const displayName = screen.getByLabelText("展示名");
    await user.type(displayName, "我的运行态");
    expect(changes.at(-1)?.display_name).toBe("我的运行态");
  });

  it("const（单值 Literal）字段预填固定值并渲染为下拉", () => {
    // pydantic 对单值 Literal 输出 const；specFromSchema 预填固定值、
    // widgetKind 按 enum 渲染为单选项下拉，避免用户手输固定值。
    const spec = specFromSchema(IN_MEMORY_RESOURCE_SCHEMAS.plugin);
    expect(spec.plugin_type).toBe("model_provider");
    expect(spec.protocol).toBe("openai_compatible");
    renderForm(IN_MEMORY_RESOURCE_SCHEMAS.plugin);
    // plugin_type / protocol 均为单值 const → 渲染为下拉（2 个 combobox），
    // 而非退化成「暂不支持的字段类型」。
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
    expect(screen.queryByText("暂不支持的字段类型")).not.toBeInTheDocument();
  });
});
