import { spawnSync } from "node:child_process";

const args = process.argv.slice(2);
const vitestArgs = args[0] === "--" ? args.slice(1) : args;

const result = spawnSync("vitest", ["run", ...vitestArgs], {
  shell: true,
  stdio: "inherit"
});

process.exit(result.status ?? 1);
