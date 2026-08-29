// TASK-016 静态质量门禁（RULE-frontend-quality-001）：TS 无 any/@ts-ignore 滥用。
// 覆盖 chat/console/shared 全部源码（排除测试目录——测试内的窄化 as 断言不受限）。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const targets = [
  { app: "shared", dir: resolve(root, "packages/shared/src") },
  { app: "console", dir: resolve(root, "apps/console/src") },
  { app: "chat", dir: resolve(root, "apps/chat/src") }
];
const BANNED = [
  // 显式 any 与抑制器（项目规则：TS 禁止 any、滥用 @ts-ignore）
  /:\s*any\b/,
  /as\s+any\b/,
  /@ts-ignore/,
  /@ts-nocheck/
];
let failed = false;

function walk(dir, visitor) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      walk(full, visitor);
      continue;
    }
    visitor(full);
  }
}

for (const { app, dir } of targets) {
  walk(dir, (file) => {
    if (!/\.(ts|tsx)$/.test(file)) return;
    const rel = relative(root, file);
    if (/\.test\.(ts|tsx)$/.test(rel)) return; // 测试文件不受限（含自检用字符串）
    if (rel.includes("__tests__") || rel.includes("/test/")) return; // 测试与测试辅助不受限
    const source = readFileSync(file, "utf8");
    for (const line of source.split("\n")) {
      for (const pattern of BANNED) {
        if (pattern.test(line)) {
          console.error(`[ts-hygiene] ${rel}: 禁用模式 ${pattern} → ${line.trim().slice(0, 80)}`);
          failed = true;
        }
      }
    }
  });
}

if (failed) {
  process.exit(1);
}
console.log("[ts-hygiene] OK");
