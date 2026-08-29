// TASK-001 静态合规门禁：组件/服务层零裸 fetch（RULE-fluxion-console-api-001）。
// envelope 消费唯一路径 = packages/shared httpClient；业务代码只经 services/api client。
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const apps = ["console", "chat"].map((a) => resolve(root, "apps", a));
const BARE_FETCH = /(^|[^.\w])fetch\(/;
let failed = false;

for (const app of apps) {
  const violations = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      if (name.startsWith("__tests__") || name === "test") continue;
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(name)) continue;
      const source = readFileSync(full, "utf8");
      if (BARE_FETCH.test(source)) violations.push(full);
    }
  };
  walk(join(app, "src"));
  for (const violation of violations) {
    console.error(`[no-bare-fetch] ${violation}: 禁止裸 fetch，须走 services/httpClient`);
    failed = true;
  }
}

if (failed) {
  process.exit(1);
}
console.log("[no-bare-fetch] OK");
