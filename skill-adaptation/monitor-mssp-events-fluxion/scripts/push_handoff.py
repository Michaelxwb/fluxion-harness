"""按监控分类为 GPT/AV 事件同步调用内容准备入口；不发送 Webhook。"""

import json
import os
import subprocess
import sys
from pathlib import Path


PUSH_SKILL = "mssp-event-push-content"
PREPARE_TIMEOUT_SECONDS = 180
ARTIFACT_KEYS = ("analysis_input", "gpt_input", "basic_info", "event_context")


def resolve_push_entry() -> Path:
    """默认同级安装；不同布局由运行环境明确提供已启用的依赖目录。"""
    configured = os.environ.get("MSSP_EVENT_PUSH_SKILL_DIR")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / PUSH_SKILL
    entry = root.resolve() / "scripts" / "run.py"
    if not entry.is_file():
        raise RuntimeError("未找到 mssp-event-push-content 入口，请安装并启用依赖 Skill")
    return entry


def build_context(row: dict) -> dict:
    """保留事件原名；CGID 匹配名单独使用第一条展示告警。"""
    context = {key: value for key, value in row.items() if key not in ("webhook_url", "webhook_push")}
    context["tenantId"] = row.get("tenant_id")
    required = ("event_id", "company_id")
    if row.get("event_type", "gpt") == "gpt":
        required += ("tenant_id", "checkout_time")
    for key in required:
        if not str(context.get(key) or "").strip() or context[key] == "-":
            raise ValueError(f"缺少 {key}，无法自动进入内容准备")
    titles = row.get("display_titles") or []
    if row.get("event_type", "gpt") == "gpt" and (not titles or not str(titles[0]).strip()):
        raise ValueError("缺少首条关联告警名称，无法自动查询 CGID")
    return context


def validate_result(stdout: str, event_id: str, event_type="gpt") -> dict:
    result = json.loads(stdout)
    if not isinstance(result, dict) or result.get("status") != "ready_for_analysis":
        raise ValueError("内容准备入口未返回 ready_for_analysis")
    if result.get("event_type", "gpt") != event_type:
        raise ValueError("内容准备返回事件类型不一致")
    if result.get("event_id") != event_id or (event_type == "gpt" and not result.get("cgid")):
        raise ValueError("内容准备入口返回的事件 ID 或 CGID 无效")
    artifact_keys = ARTIFACT_KEYS if event_type == "gpt" else ("av_summary", "event_context", "asset_context")
    for key in artifact_keys:
        path = result.get(key)
        if not isinstance(path, str) or not path or not Path(path).is_file():
            raise ValueError(f"内容准备缺少分析产物 {key}")
        if Path(path).stat().st_size == 0:
            raise ValueError(f"内容准备产物为空 {key}")
    keys = ("status", "event_id", *artifact_keys)
    if event_type == "gpt":
        keys += ("cgid",)
    validated = {key: result[key] for key in keys}
    for key in ("event_type", "event_dir"):
        if key in result:
            validated[key] = result[key]
    for key in ("delivery_state", "webhook_required", "completion_status"):
        if key in result:
            validated[key] = result[key]
    return validated


def child_failure_detail(stderr: str, returncode: int) -> str:
    """仅把已知错误映射为固定文案，不回显子进程的参数、凭据或日志。"""
    for prefix, message in (
        ("错误: 未能自动查询到 CGID", "自动查询 CGID 失败，未取得可用的 CGID"),
        ("错误: 未取得可保存的 GPTRawLog", "未取得可保存的终端GPT原始日志"),
        ("错误: 查询最新 GPTRawLog失败", "查询终端GPT原始日志失败"),
        ("错误: 解析 GPTRawLog失败", "解析终端GPT原始日志失败"),
    ):
        if any(line.startswith(prefix) for line in stderr.splitlines()):
            return message
    return f"内容准备入口失败（退出码 {returncode}）"


def prepare_event(row: dict, entry: Path, webhook_url: str = "") -> dict:
    context = build_context(row)
    event_type = row.get("event_type", "gpt")
    command = [sys.executable, str(entry), "--event-type", event_type,
               "--event-id", str(context["event_id"]),
               "--event-context-json", json.dumps(context, ensure_ascii=False)]
    if event_type == "gpt":
        command += ["--tenant", str(context["tenant_id"]),
                    "--event-name", str(row["display_titles"][0])]
    try:
        env = dict(os.environ)
        env.pop("MSSP_EVENT_WEBHOOK_URL", None)
        if webhook_url:
            env["MSSP_EVENT_WEBHOOK_URL"] = webhook_url
        completed = subprocess.run(
            command, cwd=str(entry.parent.parent), capture_output=True,
            text=True, timeout=PREPARE_TIMEOUT_SECONDS if event_type == "gpt" else 1800, check=False, env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("内容准备调用超时") from None
    if completed.returncode != 0:
        # 子进程诊断可能包含请求参数或凭据，不直接转发原始 stdout/stderr。
        raise RuntimeError(child_failure_detail(completed.stderr, completed.returncode))
    result = validate_result(completed.stdout, str(context["event_id"]), event_type)
    if webhook_url and (result.get("webhook_required") is not True or not result.get("delivery_state")):
        raise RuntimeError("下游未登记 Webhook 必发要求，交付未完成")
    return result


def prepare_events(rows: list[dict], webhook_url: str = "") -> tuple[list[dict], list[dict]]:
    selected = [row for row in rows if row.get("event_type") in ("gpt", "av")
                or ("event_type" not in row and row.get("is_gpt") is True)]
    if not selected:
        return [], []
    try:
        entry = resolve_push_entry()
    except RuntimeError as exc:
        return [], [{"event_id": row["event_id"], "error": str(exc)} for row in selected]
    prepared, failures = [], []
    for row in selected:
        try:
            prepared.append(prepare_event(row, entry, webhook_url) if webhook_url else prepare_event(row, entry))
        except (ValueError, RuntimeError) as exc:
            failures.append({"event_id": row["event_id"], "error": str(exc)})
        except OSError:
            failures.append({"event_id": row["event_id"], "error": "内容准备执行或产物读取失败"})
    return prepared, failures


def format_handoff(prepared: list[dict]) -> str:
    """这是供当前 Agent 继续研判的工具结果，不能当作推送完成回执。"""
    return json.dumps({
        "status": "ready_for_analysis",
        "next_skill": PUSH_SKILL,
        "next_step": "读取 mssp-event-push-content/SKILL.md，按 event_type 读取对应分支说明，继续共同推送判定及分支后续流程；不要重复查询解析。",
        "delivery_rule": "webhook_required=true 的事件必须调用 finalize_delivery.py --event-dir <事件目录> --url <已授权地址或key> --text-file <定稿> [--image <摘要图>]，收到 delivered 并通过 --check 后才算完成。地址从本轮授权上下文取得，不写入文件；pending_delivery 不是完成。",
        "events": prepared,
    }, ensure_ascii=False, indent=2)


def format_failure_notice(failures: list[dict], rows: list[dict]) -> str:
    customers = {row["event_id"]: row.get("company") for row in rows}
    notices = []
    for failure in failures:
        event_id = failure["event_id"]
        customer = customers.get(event_id) or "客户信息未取得"
        notices.append(
            f"事件推送内容生成失败\n客户：{customer}\n事件 ID：{event_id}\n"
            f"失败阶段：后续内容准备\n原因：{failure['error']}。\n"
            "本次未完成该事件的研判与推送文案生成，监控结果仍有效。"
        )
    return "\n\n".join(notices)
