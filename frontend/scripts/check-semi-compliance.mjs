// TASK-012 静态合规门禁：semi react19-adapter 首导 + 禁第二套通用 UI 库。
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const apps = ["console", "chat"].map((a) => resolve(root, "apps", a));
const BANNED_DEPS = [
  "antd",
  "@ant-design/icons",
  "@ant-design/pro-components",
  "@mui/material"
];
let failed = false;

for (const app of apps) {
  const mainPath = resolve(app, "src/main.tsx");
  const firstImport = readFileSync(mainPath, "utf-8")
    .split("\n")
    .find((line) => line.trim().startsWith("import"))
    ?.trim();
  if (!firstImport || !firstImport.includes("@douyinfe/semi-ui/react19-adapter")) {
    console.error(`[semi-compliance] ${mainPath}: react19-adapter 必须是第一条 import`);
    failed = true;
  }
  const pkg = JSON.parse(readFileSync(resolve(app, "package.json"), "utf-8"));
  const deps = { ...pkg.dependencies, ...pkg.devDependencies };
  for (const banned of BANNED_DEPS) {
    if (deps[banned] !== undefined) {
      console.error(`[semi-compliance] ${app}: 禁用第二套通用 UI 库 ${banned}`);
      failed = true;
    }
  }
}

if (failed) {
  process.exit(1);
}
console.log("[semi-compliance] OK");
