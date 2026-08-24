// Guard: the production bundle must never import an InMemory API.
// The console/chat production entries use real HTTP clients; InMemoryConsoleApi /
// InMemoryChatApi are test fixtures only. Any non-test source file that imports
// them means the product is silently running on in-memory data — fail the build.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const apps = ["apps/console", "apps/chat"];
const SKIP_DIRS = new Set([
  "node_modules",
  "test",
  "__tests__",
  "dist",
  "coverage",
  "build",
]);
// Matches `from "<...>inMemory...>` import specifiers, including dynamic/type imports.
const INMEMORY_IMPORT_RE = /from\s+["'][^"']*inMemory[\w/.-]*["']/i;

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      yield* walk(full);
    } else if (full.endsWith(".ts") || full.endsWith(".tsx")) {
      yield full;
    }
  }
}

let violations = 0;
for (const app of apps) {
  for (const file of walk(join(root, "frontend", app, "src"))) {
    const source = readFileSync(file, "utf8");
    for (const line of source.split("\n")) {
      if (INMEMORY_IMPORT_RE.test(line)) {
        console.error(`FAIL ${relative(root, file)}: imports an InMemory API\n  ${line.trim()}`);
        violations += 1;
      }
    }
  }
}

if (violations > 0) {
  console.error(
    `Blocked production build: ${violations} non-test file(s) import an InMemory API. ` +
      "Production entries must use the real HTTP clients (httpConsoleApi / httpChatApi)."
  );
  process.exit(1);
}
console.log("InMemory guard: PASS (no production source imports an InMemory API)");
