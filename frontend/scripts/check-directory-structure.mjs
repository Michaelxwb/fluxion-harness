// TASK-004 静态合规门禁（RULE-frontend-directory-001）：
// 页面入 src/pages/、通用组件入 src/components/ 或 shared、测试目录与源码同构、
// 组件无越界 import（跨 App 仅经 @fluxion/shared）。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const apps = ["console", "chat"].map((a) => ({ app: a, dir: resolve(root, "apps", a) }));
const APP_ROOT_FILES = new Set(["App.tsx", "main.tsx", "index.ts", "theme.ts"]);
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

for (const { app, dir } of apps) {
  const src = join(dir, "src");

  // 1) 组件/页面文件落位：src 根仅允许应用壳文件；页面在 pages/、通用组件在 components/
  walk(src, (file) => {
    if (!file.endsWith(".tsx")) return;
    const rel = relative(src, file);
    if (APP_ROOT_FILES.has(rel)) return;
    if (rel.includes("__tests__")) return; // 测试内联组件不受限
    if (rel.startsWith("test/")) return; // 测试辅助（renderX/fixtures/setup）
    if (!rel.startsWith("pages/") && !rel.startsWith("components/")) {
      console.error(`[directory] ${app}: ${rel} 应位于 src/pages/ 或 src/components/`);
      failed = true;
    }
  });

  // 2) 越界 import：跨 App 仅允许 @fluxion/shared
  walk(src, (file) => {
    if (!/\.(ts|tsx)$/.test(file)) return;
    const source = readFileSync(file, "utf8");
    const rel = relative(src, file);
    for (const match of source.matchAll(/from\s+["']([^"']+)["']/g)) {
      const spec = match[1];
      const crossApp = apps.find((a) => spec.includes(`apps/${a.app}`) || spec === `@fluxion/${a.app}`);
      if (crossApp && crossApp.app !== app) {
        console.error(`[directory] ${app}: ${rel} 越界 import ${spec}（跨 App 仅允许 @fluxion/shared）`);
        failed = true;
      }
    }
  });

  // 3) 测试目录与源码同构：*.test.* 一律位于 __tests__/ 目录
  walk(src, (file) => {
    const rel = relative(src, file);
    if (/\.test\.(ts|tsx)$/.test(rel) && !rel.includes("__tests__")) {
      console.error(`[directory] ${app}: ${rel} 应位于 __tests__/ 目录（测试与源码同构）`);
      failed = true;
    }
  });
}

if (failed) {
  process.exit(1);
}
console.log("[directory] OK");
