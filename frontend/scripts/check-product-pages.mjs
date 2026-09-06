// Guard: §10（remediation golden-path-closure TASK-026）产品页架构检查——
// 1. pages/** 禁止 import SchemaForm/SpecForm（internal/debug 除外）：
//    产品创建一律走 CreateXxxModal（产品语义端点），内联 SchemaForm 是
//    "万能表单"回流通道。
// 2. pages/** 禁止前端生成 Resource ID / 写死 version：资源 id/version 由
//    服务端生成（产品端点契约），前端随机 id 破坏 Registry 唯一性治理。
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pagesDir = join(root, "apps/console/src/pages");
const violations = [];

const SCHEMA_FORM_RE = /from\s+["'][^"']*(SchemaForm|SpecForm)[\w/.-]*["']/;
const RANDOM_ID_RE = /(Math\.random\(\)|uuidv4\(\)|crypto\.randomUUID)/;
const HARDCODED_VERSION_RE = /version:\s*["']1["']/;

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "__tests__") continue;
      yield* walk(full);
    } else if (full.endsWith(".tsx") || full.endsWith(".ts")) {
      yield full;
    }
  }
}

for (const file of walk(pagesDir)) {
  const rel = relative(root, file);
  const text = readFileSync(file, "utf-8");
  if (SCHEMA_FORM_RE.test(text)) {
    violations.push(`${rel}: pages 禁止 import SchemaForm/SpecForm（产品创建走 CreateXxxModal）`);
  }
  if (RANDOM_ID_RE.test(text)) {
    violations.push(`${rel}: pages 禁止前端生成随机 Resource ID（服务端生成）`);
  }
  // 写死 version 仅在「创建资源」payload 上下文里算违规（宽松匹配 create 调用附近）
  const createCalls = text.match(/createResource\(\{[\s\S]{0,400}?\}\)/g) ?? [];
  for (const call of createCalls) {
    if (HARDCODED_VERSION_RE.test(call)) {
      violations.push(`${rel}: createResource payload 禁止写死 version（服务端生成）`);
    }
  }
}

if (violations.length > 0) {
  console.error("[check-product-pages] 违规：");
  for (const violation of violations) console.error(`  - ${violation}`);
  process.exit(1);
}
console.log("[check-product-pages] PASS");
