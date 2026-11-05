"""把监控里归为「其他」的事件交接给 c2 处置链路，生成只读处置通告。

设计边界（用户 2026-11-02 定）：

- **只读**：固定传 `--no-actions`，绝不向客户终端下发查杀/封堵。
- **静默跳过**：c2 的 exit 2 表示「事件类型不在触发范围 / IOC 是文件 hash」，
  属于正常不适用，不进失败通知、不打断本轮。
- **复用已查结果**：把监控已经取到的事件详情、资产候选、告警标题写成
  `--event-json` 交给 c2，避免同一事件重复查询。
- 不改动 gpt/av 两条既有链路，只处理 `event_type == "other"`。

2026-11-04 变更：原独立 skill `c2-virus-push` 已并入 `mssp-event-push-content`
（代码在 `scripts/c2/`）。本模块因此**优先找合并后的入口**
`<mssp-event-push-content>/scripts/c2/run.py`，找不到才回退老的独立 skill。
入口的 CLI 契约与退出码语义**完全没变**（仍是 `--event-id/--company-id/
--event-json/--no-actions/--no-hunt`，stdout 仍是通告正文，exit 2 仍表示不适用），
所以 `prepare_one()` 与下游推送逻辑一行都不用改。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# 合并后 c2 归属的宿主 skill 名（2026-11-04）
C2_HOST_SKILL = "mssp-event-push-content"
# 合并前的独立 skill 名，仅作兼容回退（老环境还没升级宿主时仍能用）
C2_SKILL = "c2-virus-push"
HANDOFF_TIMEOUT_SECONDS = 300
NOT_APPLICABLE_EXIT = 2
RELEVANT_EVENT_TYPE = "other"


def resolve_c2_entry() -> tuple[Path, Path]:
    """定位 c2 入口，返回 (入口脚本, 运行目录)。

    顺序（2026-11-04 合并后调整）：
      1. `C2_PUSH_SKILL_DIR` 显式指定：先试 `<dir>/scripts/c2/run.py`（合并后布局），
         再试 `<dir>/scripts/run.py`（合并前布局）
      2. 同级安装的宿主 skill `<mssp-event-push-content>/scripts/c2/run.py`
      3. `/opt` 已生效的宿主 skill 同上
      4. 回退：同级 / `/opt` 下老的独立 `c2-virus-push`

    ⚠️ 合并后**必须优先新路径**：宿主升级后老独立 skill 会被删掉，
    这时如果还按老路径找，监控会把每条 other 事件都报成「未找到入口」。
    """
    roots = []
    configured = os.environ.get("C2_PUSH_SKILL_DIR")
    if configured:
        roots.append(Path(configured))
    here = Path(__file__).resolve()
    roots.append(here.parents[2] / C2_HOST_SKILL)
    roots.append(Path("/opt/openclaw-skills") / C2_HOST_SKILL)
    roots.append(here.parents[2] / C2_SKILL)
    roots.append(Path("/opt/openclaw-skills") / C2_SKILL)

    for root in roots:
        # 合并后布局：<skill>/scripts/c2/run.py，运行目录取 skill 根
        merged = (root / "scripts" / "c2" / "run.py").resolve()
        if merged.is_file():
            return merged, root.resolve()
        # 合并前布局：<skill>/scripts/run.py
        legacy = (root / "scripts" / "run.py").resolve()
        if legacy.is_file():
            return legacy, root.resolve()
    raise RuntimeError(
        "未找到 c2 入口（应在 mssp-event-push-content/scripts/c2/run.py），"
        "请安装并启用依赖 Skill")


def select_rows(rows: list[dict]) -> list[dict]:
    """只挑非 GPT、非 AV 的事件；其余两条链路各自处理，不重复。"""
    return [row for row in rows if row.get("event_type") == RELEVANT_EVENT_TYPE]


def _pick_asset(row: dict, assets: list) -> dict:
    """从候选资产里挑出**与事件主机 IP 精确相等**的那一条。

    ⚠️ 不能直接把 candidates[0] 交出去：资产接口的 `ip_url_keyword` 是子串匹配，
    查 172.16.5.2 会同时命中 172.16.5.225 等，取首条等于随机抓一台机器，
    会把别的主机名/MAC/责任人写进客户通告（c2 里为此专门做过精确比对）。
    """
    host_ip = str(row.get("host_ip") or "").strip()
    for item in assets:
        if str(item.get("asset") or "").strip() == host_ip:
            return item
    return {}


def build_event_payload(row: dict) -> dict:
    """把监控已取得的结果压成交接契约，缺什么就少给什么（c2 会自己补查）。

    契约（c2-virus-push `load_event_json` 读取）：
      {"event": {<事件详情 data>, "asset": {<单条资产>}, "alarms": [<标题>]}

    ⚠️ 键名必须是单数 `asset`：c2 读的是 `d.get("asset")`。早期这里写的是
    复数 `assets` 列表，c2 一直读不到 → 每次都白跑一次资产查询（且没有报错，
    属于静默失效）。
    """
    snapshot = row.get("source_snapshot") or {}
    detail = snapshot.get("detail")
    assets = snapshot.get("assets")
    alarms = row.get("all_titles")
    payload = {}
    if isinstance(detail, dict) and detail:
        payload["event"] = detail
    if isinstance(assets, list):
        candidates = [a for a in assets if isinstance(a, dict)]
        matched = _pick_asset(row, candidates)
        if matched:
            payload["asset"] = matched
    if isinstance(alarms, list):
        titles = [str(t) for t in alarms if str(t).strip()]
        if titles:
            payload["alarms"] = titles
    return payload


def _read_card_manifest(event_id: str) -> str:
    """读 c2 落盘的《处置跟进卡》路径；没有卡片就返回空串。

    c2 把卡片写在 `<SKILL_OUTPUT_DIR>/cards/<事件ID>/` 下，并在同目录留
    `card_manifest.json`。子进程继承同一个 SKILL_OUTPUT_DIR，所以这里能直接取。
    卡片是**附加产物**：没生成不算失败，只是不推图。
    """
    root = os.environ.get("SKILL_OUTPUT_DIR")
    if not root or not event_id:
        return ""
    manifest = os.path.join(root, "cards", str(event_id), "card_manifest.json")
    if not os.path.isfile(manifest):
        return ""
    try:
        with open(manifest, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or not data.get("ok"):
        return ""
    png = str((data.get("outputs") or {}).get("png") or "").strip()
    return png if png and os.path.isfile(png) else ""


def _last_line(text: str, limit: int = 220) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "")[:limit]


def prepare_one(row: dict, entry, hunt: bool = True, run_dir=None) -> dict:
    """跑一个事件；返回 {'event_id','notice'} 或 {'event_id','skipped'}。

    `entry` 接受两种形态（2026-11-04 合并后新增元组形态，保持向后兼容）：
      - `(入口脚本, 运行目录)`：`resolve_c2_entry()` 的返回值；
      - 单个 `Path`：老用法 / 测试用，运行目录默认取入口的上两级。
    """
    event_id = str(row.get("event_id") or "").strip()
    company_id = str(row.get("company_id") or "").strip()
    if not event_id or not company_id.isdigit():
        raise ValueError("事件 ID 或数字 company_id 缺失，无法交接")

    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        entry, run_dir = Path(entry[0]), Path(entry[1])
    else:
        entry = Path(entry)
        run_dir = Path(run_dir) if run_dir else entry.parent.parent

    payload = build_event_payload(row)
    event_file = ""
    if payload:
        # 交接中间文件必须落在 guard 注入的 SKILL_OUTPUT_DIR 下（per-user 隔离）。
        root = os.environ.get("SKILL_OUTPUT_DIR")
        if not root:
            raise RuntimeError("SKILL_OUTPUT_DIR 未注入，无法写入交接中间结果")
        work_dir = os.path.join(root, "c2_handoff")
        os.makedirs(work_dir, exist_ok=True)
        event_file = os.path.join(work_dir, f"{event_id}_event.json")
        with open(event_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    command = [sys.executable, str(entry),
               "--event-id", event_id,
               "--company-id", company_id,
               "--no-actions"]
    if event_file:
        command += ["--event-json", event_file]
    # 默认**执行**全网回溯摸排（2026-11-02 用户定：不要永远不排查）。
    # 只有显式 hunt=False 才跳；跳过时 c2 侧会打 WARNING，通告里也就没有「全网排查」这一行。
    if not hunt:
        command.append("--no-hunt")

    try:
        completed = subprocess.run(
            command, cwd=str(run_dir), capture_output=True,
            text=True, timeout=HANDOFF_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("处置通告生成超时") from None

    if completed.returncode == NOT_APPLICABLE_EXIT:
        return {"event_id": event_id, "skipped": True,
                "reason": _last_line(completed.stderr) or "事件不在 C2 处置范围"}
    if completed.returncode != 0:
        raise RuntimeError(_last_line(completed.stderr) or
                           f"处置通告生成失败（退出码 {completed.returncode}）")
    notice = (completed.stdout or "").strip()
    if not notice:
        raise RuntimeError("处置通告入口未输出正文")
    return {"event_id": event_id, "notice": notice,
            "card": _read_card_manifest(event_id)}


def prepare_events(rows: list[dict], hunt: bool = True):
    """逐个事件生成通告：单事件失败不影响其他事件。

    hunt 默认 True：每个事件都会跑一次 related_entity_hunt（约 3~10 秒/事件，
    且走检索中心查询配额）。要关掉用 monitor 的 `--no-c2-hunt`。
    """
    selected = select_rows(rows)
    if not selected:
        return [], [], []
    try:
        entry = resolve_c2_entry()
    except RuntimeError as exc:
        return [], [], [{"event_id": row.get("event_id", ""), "error": str(exc)} for row in selected]

    prepared, skipped, failures = [], [], []
    for row in selected:
        try:
            result = prepare_one(row, entry, hunt=hunt)
        except (ValueError, RuntimeError) as exc:
            failures.append({"event_id": row.get("event_id", ""), "error": str(exc)})
            continue
        except OSError:
            failures.append({"event_id": row.get("event_id", ""), "error": "处置通告执行或读取失败"})
            continue
        if result.get("skipped"):
            skipped.append(result)
        else:
            prepared.append(result)
    return prepared, skipped, failures
