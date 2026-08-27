import type { JsonSchemaNode } from "../types/console";

/**
 * ADR-012：表单 schema 的真相源是后端 `GET /api/v1/resources/{type}/schema`
 * （pydantic model_json_schema()）。本文件仅供 inMemory ConsoleApi（离线开发/
 * 组件测试）内嵌镜像，线上构建不打包（check-no-inmemory）。
 *
 * 镜像只保留表单消费的结构子集（type/anyOf/const/enum/default/items/$ref/$defs/
 * additionalProperties/title/description/required）；约束键（min/max*、
 * exclusiveMinimum 等）对表单无意义，略。改 contracts.py 后须同步本镜像的字段
 * 形状——尤其 Optional 字段用 anyOf、单值 Literal 用 const、$defs 键用类名，
 * 与 pydantic 实际输出一致（否则单测会与真后端漂移：曾因镜像把 Optional 写成
 * type:[string,null] 而真 UI 的 anyOf 字段退化成「暂不支持的字段类型」、
 * 单测却全绿）。
 */
export const IN_MEMORY_RESOURCE_SCHEMAS: Readonly<Record<string, JsonSchemaNode>> = {
  runtime_profile: {
    title: "RuntimeProfile",
    type: "object",
    required: ["prompt"],
    properties: {
      display_name: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "展示名", description: "展示名（仅 UI 显示，运行时不消费）" },
      prompt: { type: "string", title: "系统提示词", description: "System Prompt：助手的人格与行为准则" },
      model_policy: { $ref: "#/$defs/ModelPolicy", title: "模型链与超时策略", description: "模型链与超时策略" },
      allowed_skills: { type: "array", items: { type: "string" }, title: "挂载的技能", description: "挂载的技能资源（id 或 id@version）" },
      allowed_mcps: { type: "array", items: { type: "string" }, title: "挂载的 MCP", description: "挂载的 MCP server（还需用户级 binding 授权）" },
      allowed_tools: { type: "array", items: { type: "string" }, title: "工具白名单", description: "agent 工具白名单（tool id）" },
      plugin_bindings: { type: "array", items: { type: "string" }, title: "模型供应商插件", description: "挂载的模型供应商插件（密钥配在 plugin binding）" },
      guardrail_policy: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "策略引用", description: "策略资源引用（id@version）；执行期仅锚定版本进快照" }
    },
    $defs: {
      ModelPolicy: {
        title: "ModelPolicy",
        type: "object",
        properties: {
          provider: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "主供应商", description: "主模型供应商 plugin_id（插件资源 ID）" },
          failover: { type: "array", items: { type: "string" }, title: "降级链", description: "主供应商失败时的降级链（plugin_id 列表）" },
          model: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "模型名", description: "模型名；留空则用 provider 默认模型" },
          timeout_ms: { type: "integer", default: 60000, title: "单次调用超时", description: "单次模型调用超时（毫秒）" },
          deadline_ms: { type: "integer", default: 120000, title: "执行截止", description: "整次执行截止时间（毫秒）" },
          max_rounds: { type: "integer", default: 8, title: "轮数上限", description: "agent 工具循环轮数上限（最大 32）" }
        }
      }
    }
  },
  skill: {
    title: "SkillDefinition",
    type: "object",
    required: ["name"],
    properties: {
      name: { type: "string", title: "技能名", description: "技能名（展示用）" },
      instructions: { type: "string", default: "", title: "做法说明", description: "固化给助手的任务做法；注入 system prompt" },
      allowed_tools: { type: "array", items: { type: "string" }, title: "放行工具", description: "该技能放行的工具（并入 agent 工具白名单）" }
    }
  },
  tool: {
    type: "object",
    additionalProperties: false,
    required: ["name", "capability_ref"],
    properties: {
      name: { type: "string", title: "工具名", description: "工具展示名" },
      description: { type: "string", title: "说明", description: "工具用途说明" },
      capability_ref: { type: "string", title: "能力引用", description: "Tool Adapter 复用的 Capability ID" },
      adapter_ref: { type: "string", title: "适配器引用", description: "具体 Adapter/Provider 的版本化引用" },
      timeout_ms: { type: "integer", title: "调用超时", default: 30000 },
      fail_policy: {
        title: "失败策略",
        anyOf: [
          { const: "fail_open", title: "失败放行" },
          { const: "fail_closed", title: "失败拦截" }
        ]
      }
    }
  },
  mcp: {
    title: "MCPDefinition",
    type: "object",
    required: ["name", "transport"],
    properties: {
      name: { type: "string", title: "MCP 名", description: "MCP server 名（展示用）" },
      display_name: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "展示名", description: "展示名（仅 UI 显示）" },
      transport: { type: "string", enum: ["stdio", "streamable_http"], title: "连接方式", description: "连接方式：stdio（本地进程）或 streamable_http（远程服务）" },
      command: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "启动命令", description: "stdio 必填：启动命令（如 npx / python）" },
      args: { type: "array", items: { type: "string" }, title: "命令参数", description: "stdio：命令参数" },
      env: { type: "object", additionalProperties: { type: "string" }, title: "环境变量", description: "stdio：环境变量（密钥不要写这里）" },
      cwd: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "工作目录", description: "stdio：工作目录" },
      url: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "服务地址", description: "streamable_http 必填：服务地址（https://…/mcp）" },
      headers: { type: "object", additionalProperties: { type: "string" }, title: "请求头", description: "streamable_http：附加请求头（密钥不要写这里）" },
      credential_env: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "密钥环境变量", description: "stdio：binding 密钥注入到的环境变量名（如 API_KEY）" },
      credential_header: { type: "string", default: "Authorization", title: "密钥请求头", description: "streamable_http：binding 密钥注入到的请求头名" },
      credential_scheme: { type: "string", default: "Bearer", title: "请求头前缀", description: "streamable_http：请求头前缀（如 Bearer）" },
      timeout_ms: { type: "integer", default: 30000, title: "连接超时", description: "连接与读超时（毫秒）" },
      allowed_tools: { type: "array", items: { type: "string" }, title: "工具白名单", description: "server 工具白名单；留空放行全部已发现工具" }
    }
  },
  plugin: {
    title: "ModelProviderDefinition",
    type: "object",
    required: ["plugin_type", "protocol", "base_url", "model"],
    properties: {
      plugin_type: { type: "string", const: "model_provider", title: "插件类型", description: "插件类型（固定 model_provider）" },
      protocol: { type: "string", const: "openai_compatible", title: "协议", description: "协议（固定 openai_compatible）" },
      base_url: { type: "string", title: "API 地址", description: "OpenAI 兼容 API 地址（http/https）" },
      model: { type: "string", title: "默认模型", description: "默认模型名（如 deepseek-chat）" },
      request_timeout_ms: { type: "integer", default: 60000, title: "请求超时", description: "请求超时（毫秒）" },
      max_retries: { type: "integer", default: 1, title: "重试次数", description: "失败重试次数" }
    }
  },
  policy: {
    title: "PolicyDefinition",
    type: "object",
    required: ["name"],
    properties: {
      name: { type: "string", title: "策略名", description: "策略名（展示用）" },
      allowed_tools: { type: "array", items: { type: "string" }, title: "工具白名单", description: "租户工具白名单；非空时仅放行所列工具，留空则不限定" },
      denied_tools: { type: "array", items: { type: "string" }, title: "工具黑名单", description: "租户工具黑名单；始终优先于白名单拒绝" }
    }
  },
  workflow: {
    title: "WorkflowDefinition",
    type: "object",
    required: ["name", "engine_ref", "steps"],
    properties: {
      name: { type: "string", title: "工作流名", description: "工作流名" },
      description: { type: "string", default: "", title: "说明", description: "说明" },
      display_name: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "展示名", description: "展示名（仅 UI 显示）" },
      engine_ref: { type: "string", title: "执行引擎", description: "执行引擎引用（workflow-engine:// 前缀）" },
      steps: { type: "array", items: { $ref: "#/$defs/WorkflowStepDefinition" }, title: "步骤", description: "步骤序列（1-200 步）" }
    },
    $defs: {
      WorkflowStepDefinition: {
        title: "WorkflowStepDefinition",
        type: "object",
        required: ["id", "capability_ref"],
        properties: {
          id: { type: "string", title: "步骤 ID", description: "步骤 ID（流程内唯一）" },
          capability_ref: { type: "string", title: "能力引用", description: "能力引用，格式 (skill|mcp|plugin):<id>@<version>" },
          depends_on: { type: "array", items: { type: "string" }, title: "前置步骤", description: "前置步骤 ID（须无环）" },
          input: { type: "object", additionalProperties: true, title: "静态输入", description: "步骤静态输入" }
        }
      }
    }
  }
};
