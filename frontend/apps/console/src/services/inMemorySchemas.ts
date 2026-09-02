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
    required: ["request_timeout_ms", "max_retries"],
    properties: {
      request_timeout_ms: { type: "integer", title: "请求超时", description: "外部调用超时（毫秒）" },
      max_retries: { type: "integer", title: "重试上限", description: "失败后的有限重试次数" },
      max_rounds: { type: "integer", default: 8, title: "轮数上限" },
      concurrency: { type: "integer", default: 1, title: "并发上限" },
      memory_budget_mb: { type: "integer", default: 512, title: "内存预算" },
      bootstrapped_from: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "自举来源" }
    }
  },
  model_provider: {
    title: "ProviderDefinition",
    type: "object",
    required: ["protocol", "base_url", "credential_ref"],
    properties: {
      protocol: { type: "string", const: "openai-compatible", title: "协议" },
      base_url: { type: "string", title: "API 地址" },
      credential_ref: { type: "string", title: "凭据引用" },
      default_model: { anyOf: [{ type: "string" }, { type: "null" }], default: null, title: "默认模型" },
      request_timeout_ms: { type: "integer", default: 60000, title: "请求超时" },
      max_retries: { type: "integer", default: 1, title: "重试次数" }
    }
  },
  model_definition: {
    title: "ModelDefinition",
    type: "object",
    required: ["name", "provider_ref"],
    properties: {
      name: { type: "string", title: "模型名" },
      provider_ref: { type: "object", title: "供应商引用" },
      capabilities: { type: "object", title: "模型能力" }
    }
  },
  skill: {
    title: "SkillDefinition",
    type: "object",
    required: ["name"],
    properties: {
      name: { type: "string", title: "技能名", description: "技能名（展示用）" },
      instructions: { type: "string", default: "", title: "做法说明", description: "固化给助手的任务做法；注入 system prompt" },
      required_capabilities: { type: "array", items: { type: "string" }, title: "所需能力", description: "该技能所需的能力（须由 Agent 已声明能力覆盖，不隐式扩权）" }
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
    title: "PluginDefinition",
    type: "object",
    required: ["name", "package", "trust_level"],
    properties: {
      name: { type: "string", title: "插件名" },
      package: { type: "string", title: "包" },
      trust_level: { type: "string", enum: ["trusted", "untrusted"], title: "信任级别" }
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
