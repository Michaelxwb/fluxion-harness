import { execFile } from "child_process";
import { join } from "path";
import { appendFileSync, mkdirSync } from "fs";

// Note: intentionally no `import { Plugin } from "@opencode/plugin"`.
// Plugin.define is an identity helper only; a plain default export with
// { id, setup } satisfies the V2 loader schema and keeps this plugin
// dependency-free (the server binary cannot resolve bare specifiers
// from this directory).

const SCRIPT_DIR = ".code-flow/scripts";
const STOP_HOOK_TIMEOUT = 35000;
const POST_HOOK_TIMEOUT = 5000;
const EDIT_TOOLS = new Set(["edit", "write", "multiedit", "patch"]);

// Per-session queued feedback. Single map, append-only: idle/stop-check and
// post-check feedback accumulate here and are consumed by the next
// context hook. Never overwrite — a prompt hook arriving between idle and
// context must not drop queued stop-check feedback.
const sessionContext = new Map();

export function mergePending(existing, incoming) {
  if (!existing) return incoming;
  if (!incoming) return existing;
  return existing + "\n\n" + incoming;
}

function debugLog(projectRoot, msg) {
  if (process.env.CF_DEBUG !== "1") return;
  try {
    const dir = join(projectRoot, ".code-flow");
    mkdirSync(dir, { recursive: true });
    const ts = new Date().toISOString().replace("T", " ").slice(0, 19);
    appendFileSync(join(dir, ".debug.log"), `${ts} [opencode] ${msg}\n`);
  } catch {}
}

function pythonPath(projectRoot, script) {
  return join(projectRoot, SCRIPT_DIR, script);
}

function callHook(projectRoot, script, input, timeout = POST_HOOK_TIMEOUT) {
  return new Promise((resolve) => {
    const child = execFile(
      "python3",
      [pythonPath(projectRoot, script)],
      { cwd: projectRoot, timeout, maxBuffer: 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error) {
          debugLog(projectRoot, `callHook ${script} failed: ${error.message || stderr}`);
          resolve(null);
          return;
        }
        const text = (stdout || "").trim();
        if (!text) {
          resolve(null);
          return;
        }
        try {
          resolve(JSON.parse(text));
        } catch (e) {
          debugLog(projectRoot, `callHook ${script} bad JSON: ${e.message}`);
          resolve(null);
        }
      }
    );
    if (input !== undefined && child.stdin) {
      child.stdin.write(JSON.stringify(input));
      child.stdin.end();
    }
  });
}

function readSessionID(event) {
  if (!event || typeof event !== "object") return "";
  const data = event.data && typeof event.data === "object" ? event.data : null;
  return (
    event.sessionID ||
    data?.sessionID ||
    data?.session?.id ||
    data?.info?.id ||
    event.properties?.sessionID ||
    event.properties?.info?.id ||
    ""
  );
}

function readFilePath(input) {
  if (!input || typeof input !== "object") return "";
  return input.filePath || input.file_path || input.path || "";
}

async function queueStopFeedback(projectRoot, sessionID) {
  const result = await callHook(projectRoot, "cf_stop_hook.py", { session_id: sessionID }, STOP_HOOK_TIMEOUT);
  if (result?.reason) {
    sessionContext.set(sessionID, mergePending(sessionContext.get(sessionID), result.reason));
    debugLog(projectRoot, "stop-check feedback queued");
  }
}

function subscribeSessionEvents(ctx, projectRoot, signal) {
  void (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal })) {
        try {
          if (event.type === "session.created") {
            const sid = readSessionID(event);
            debugLog(projectRoot, `session.created sid=${sid}`);
            if (sid) sessionContext.delete(sid);
          }
          if (event.type === "session.idle") {
            const sid = readSessionID(event);
            if (sid) void queueStopFeedback(projectRoot, sid);
          }
        } catch {}
      }
    } catch {}
  })();
}

async function handlePrompt(projectRoot, event) {
  const promptText = event.prompt?.text || "";
  if (!promptText) return;
  debugLog(projectRoot, `prompt sid=${event.sessionID} len=${promptText.length}`);
  const result = await callHook(projectRoot, "cf_user_prompt_hook.py", {
    prompt: promptText,
    session_id: event.sessionID,
  });
  const additional = result?.hookSpecificOutput?.additionalContext;
  if (additional) {
    debugLog(projectRoot, `hook matched — context ${additional.length} chars cached`);
    sessionContext.set(event.sessionID, mergePending(sessionContext.get(event.sessionID), additional));
  } else {
    debugLog(projectRoot, "hook returned no context");
  }
}

async function handleToolAfter(projectRoot, event) {
  const tool = String(event.tool || "").toLowerCase();
  if (!EDIT_TOOLS.has(tool)) return;
  const filePath = readFilePath(event.input);
  if (!filePath) return;
  const result = await callHook(projectRoot, "cf_post_hook.py", {
    tool_name: tool === "write" ? "Write" : "Edit",
    tool_input: { file_path: filePath },
    session_id: event.sessionID || "",
  });
  const feedback = result?.hookSpecificOutput?.additionalContext;
  if (feedback) {
    const sid = event.sessionID || "";
    sessionContext.set(sid, mergePending(sessionContext.get(sid), feedback));
    debugLog(projectRoot, `post-check feedback queued ${feedback.length} chars`);
  }
}

function injectPending(projectRoot, event) {
  const pending = sessionContext.get(event.sessionID);
  if (pending) {
    event.system.push({ type: "text", text: pending });
    debugLog(projectRoot, `context — injected ${pending.length} chars`);
    sessionContext.delete(event.sessionID);
  }
}

export default {
  id: "code-flow",
  async setup(ctx) {
    const projectRoot = ctx.location?.directory ?? process.cwd();
    const controller = new AbortController();
    subscribeSessionEvents(ctx, projectRoot, controller.signal);
    await ctx.session.hook("prompt", (event) => handlePrompt(projectRoot, event));
    await ctx.tool.hook("execute.after", (event) => handleToolAfter(projectRoot, event));
    await ctx.session.hook("context", (event) => injectPending(projectRoot, event));
    return () => controller.abort();
  },
};
