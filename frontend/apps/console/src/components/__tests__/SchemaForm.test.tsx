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
    expect(spec.max_rounds).toBe(8);
    expect(spec.concurrency).toBe(1);
    expect(spec.memory_budget_mb).toBe(512);
    expect("bootstrapped_from" in spec).toBe(false);
  });

  it("默认值直接落在顶层（Provider 的 request_timeout_ms/max_retries）", () => {
    const spec = specFromSchema(IN_MEMORY_RESOURCE_SCHEMAS.model_provider);
    expect(spec.request_timeout_ms).toBe(60000);
    expect(spec.max_retries).toBe(1);
  });
});

describe("RS7 SchemaForm 渲染", () => {
  it("必填字段带星号标记，description 作为说明展示", () => {
    renderForm(runtimeProfileSchema);
    expect(screen.getAllByText("*")).toHaveLength(2);
    expect(screen.getByText("请求超时")).toBeInTheDocument();
    expect(screen.getByText("外部调用超时（毫秒）")).toBeInTheDocument();
  });

  it("运行机制数值字段可编辑", async () => {
    const user = userEvent.setup();
    const { changes } = renderForm(runtimeProfileSchema);

    const maxRounds = screen.getByDisplayValue("8");
    await user.clear(maxRounds);
    await user.type(maxRounds, "12");

    expect(changes.at(-1)?.max_rounds).toBe(12);
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
    const { changes } = renderForm(IN_MEMORY_RESOURCE_SCHEMAS.model_provider);
    const defaultModel = screen.getByLabelText("默认模型");
    await user.type(defaultModel, "deepseek-chat");
    expect(changes.at(-1)?.default_model).toBe("deepseek-chat");
  });

  it("const（单值 Literal）字段预填固定值并渲染为下拉", () => {
    // pydantic 对单值 Literal 输出 const；specFromSchema 预填固定值、
    // widgetKind 按 enum 渲染为单选项下拉，避免用户手输固定值。
    const spec = specFromSchema(IN_MEMORY_RESOURCE_SCHEMAS.model_provider);
    expect(spec.protocol).toBe("openai-compatible");
    renderForm(IN_MEMORY_RESOURCE_SCHEMAS.model_provider);
    // protocol 为单值 const → 渲染为下拉，
    // 而非退化成「暂不支持的字段类型」。
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    expect(screen.queryByText("暂不支持的字段类型")).not.toBeInTheDocument();
  });
});
