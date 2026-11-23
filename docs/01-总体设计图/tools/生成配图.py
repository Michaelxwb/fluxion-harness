# ruff: noqa: E501
"""生成总体设计配图：统一画布、统一字号，输出 SVG、Mermaid 结构对照与布局数据。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_DIR = ROOT / "tools" / "布局数据"
PALETTE = {
    "active": ("#EFF6FC", "#B7D1E5"),
    "store": ("#F2F7F6", "#BAD6CE"),
    "external": ("#F4F6F9", "#CBD3DE"),
    "explore": ("#F5F0FC", "#A88BCB"),
    "success": ("#EDF7F0", "#A9D2B4"),
    "failure": ("#FFF1EF", "#E5B5AC"),
    "inactive": ("#F2F4F6", "#C7CED6"),
}


def units(value: str) -> float:
    return sum(0.56 if ord(char) < 128 else 1.0 for char in value)


class Canvas:
    def __init__(self, name: str, width: int = 1200, height: int = 620) -> None:
        self.name, self.width, self.height = name, width, height
        self.nodes: dict[str, dict[str, object]] = {}
        self.edges: list[dict[str, object]] = []
        self.groups: dict[str, dict[str, object]] = {}
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
            f'<title id="title">{escape(name)}</title>',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>',
            '<style>text{font-family:PingFang SC,Microsoft YaHei,Arial,sans-serif;fill:#20344B}path,polyline{stroke-linejoin:round;stroke-linecap:round}</style>',
            f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        ]

    def text(self, x: float, y: float, value: str, size: int = 16, weight: int = 400, anchor: str = "start", color: str = "#20344B") -> None:
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" style="fill:{color}">{escape(value)}</text>')

    def panel(self, key: str, x: int, y: int, w: int, h: int, title: str, explore: bool = False) -> None:
        fill, stroke = ("#FBF8FF", "#A88BCB") if explore else ("#FBFCFE", "#DCE4EC")
        dash = ' stroke-dasharray="6 5"' if explore else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.4"{dash}/>')
        self.text(x + 16, y + 27, title, 16, 600, color="#735199" if explore else "#4B637C")
        self.groups[key] = {"rect": [x, y, w, h], "title": title}

    def node(self, key: str, x: int, y: int, w: int, h: int, title: str, lines: tuple[str, ...] = (), tone: str = "active", state: bool = False) -> None:
        fill, stroke = PALETTE[tone]
        dash = ' stroke-dasharray="5 4"' if tone == "explore" else ""
        self.parts.append(f'<g data-node="{key}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>')
        title_size = 18 if state else 19
        baseline = y + 27 if lines else y + h / 2 + 6
        self.text(x + w / 2, baseline, title, title_size, 600, "middle")
        for index, line in enumerate(lines):
            self.text(x + w / 2, y + 49 + index * 19, line, 14, anchor="middle", color="#536B81")
        self.parts.append("</g>")
        self.nodes[key] = {"rect": [x, y, w, h], "label": title, "lines": list(lines), "state": state, "tone": tone}

    def edge(self, origin: str, target: str, points: list[tuple[int, int]], label: str = "", label_at: tuple[int, int] | None = None, kind: str = "main", both: bool = False) -> None:
        color = {"main": "#447DA5", "return": "#3D8585", "reply": "#3D8585", "async": "#7A8798", "control": "#91A0AF"}[kind]
        dashed = ' stroke-dasharray="5 4"' if kind in ("async", "control", "reply") else ""
        encoded = " ".join(f"{x},{y}" for x, y in points)
        start = ' marker-start="url(#arrow)"' if both else ""
        self.parts.append(f'<polyline points="{encoded}" fill="none" stroke="{color}" stroke-width="1.8" marker-end="url(#arrow)"{start}{dashed}/>')
        if label and label_at:
            self.edge_label(*label_at, label, color)
        self.edges.append({"from": origin, "to": target, "points": points, "label": label, "both": both})

    def edge_label(self, x: int, y: int, value: str, color: str) -> None:
        width = units(value) * 15 + 12
        self.parts.append(f'<rect x="{x - width / 2}" y="{y - 15}" width="{width}" height="20" rx="4" fill="#FFFFFF"/>')
        self.text(x, y, value, 15, 500, "middle", color)

    def finish(self, mermaid: bool = True) -> None:
        for node in self.nodes.values():
            x, y, w, h = node["rect"]
            for key, group in self.groups.items():
                gx, gy, gw, gh = group["rect"]
                if gx <= x and gy <= y and x + w <= gx + gw and y + h <= gy + gh:
                    node["group"] = key
                    break
        self.parts.append("</svg>")
        (ROOT / f"{self.name}.svg").write_text("\n".join(self.parts) + "\n", encoding="utf-8")
        LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
        (LAYOUT_DIR / f"{self.name}.json").write_text(
            json.dumps(self.metadata(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if mermaid:
            self.write_mermaid()

    def metadata(self) -> dict[str, object]:
        return {"name": self.name, "size": [self.width, self.height], "nodes": self.nodes, "groups": self.groups, "edges": self.edges}

    def write_mermaid(self) -> None:
        lines = ["flowchart LR"]
        for key, group in self.groups.items():
            lines.append(f'  subgraph {key}["{group["title"]}"]')
            for name, node in self.nodes.items():
                if node.get("group") == key:
                    lines.append(f'    {name}["{readable_label(node)}"]')
            lines.append("  end")
        for key, node in self.nodes.items():
            if "group" not in node:
                lines.append(f'  {key}["{readable_label(node)}"]')
        for edge in self.edges:
            label = f'|"{edge["label"]}"|' if edge["label"] else ""
            arrow = "<-->" if edge["both"] else "-->"
            lines.append(f'  {edge["from"]} {arrow}{label} {edge["to"]}')
        (ROOT / f"{self.name}.mmd").write_text("\n".join(lines) + "\n", encoding="utf-8")


def readable_label(node: dict[str, object]) -> str:
    step = node.get("step")
    return f'{step} {node["label"]}' if step else str(node["label"])


def number_badge(c: Canvas, key: str, step: str) -> None:
    x, y, _, _ = c.nodes[key]["rect"]
    color = "#447DA5" if step.isdigit() else "#6F7F90"
    c.parts.append(f'<circle cx="{x + 10}" cy="{y - 7}" r="13" fill="{color}" stroke="white" stroke-width="2"/>')
    c.text(x + 10, y - 2, step, 14, 600, "middle", "#FFFFFF")
    c.nodes[key]["step"] = step


def reading_guide(c: Canvas, first: str, second: str, y: int = 400) -> None:
    c.parts.append(f'<rect x="30" y="{y}" width="940" height="52" rx="7" fill="#F4F7FA"/>')
    c.text(47, y + 21, first, 14, 500, color="#3E5E78")
    c.text(47, y + 41, second, 14, color="#5A7085")


class SequenceCanvas(Canvas):
    def __init__(self, name: str, width: int, height: int) -> None:
        super().__init__(name, width, height)
        self.lifelines: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.notes: dict[str, str] = {}

    def scenario(self, key: str, y: int, title: str, height: int = 335) -> None:
        self.parts.append(f'<rect x="30" y="{y}" width="1140" height="{height}" rx="12" fill="#FBFCFE" stroke="#DCE4EC" stroke-width="1.4"/>')
        self.groups[key] = {"rect": [30, y, 1140, height], "title": title}

    def scenario_title(self, key: str) -> None:
        x, y, _, _ = self.groups[key]["rect"]
        title = str(self.groups[key]["title"])
        width = units(title) * 16 + 24
        self.parts.append(f'<rect x="{x + 11}" y="{y + 8}" width="{width}" height="26" fill="#FBFCFE"/>')
        self.text(x + 16, y + 27, title, 16, 600, color="#4B637C")

    def participant(self, key: str, x: int, y: int, bottom: int, title: str, detail: str, tone: str = "active") -> None:
        self.node(key, x - 80, y, 160, 60, title, (detail,), tone)
        self.parts.append(f'<line data-lifeline="{key}" x1="{x}" y1="{y + 60}" x2="{x}" y2="{bottom}" stroke="#6B8199" stroke-width="1.5"/>')
        self.parts.append(f'<circle cx="{x}" cy="{bottom}" r="3" fill="#8FA3B6"/>')
        self.lifelines.append({"participant": key, "node": key, "points": [(x, y + 60), (x, bottom)]})

    def message(self, scenario: str, order: int, origin: str, target: str, y: int, label: str, reply: bool = False) -> None:
        ox, _, ow, _ = self.nodes[origin]["rect"]
        tx, _, tw, _ = self.nodes[target]["rect"]
        source_x, target_x = int(ox + ow / 2), int(tx + tw / 2)
        self.parts.append(f'<line x1="40" y1="{y}" x2="1160" y2="{y}" stroke="#EEF3F7" stroke-width="1"/>')
        self.parts.append(f'<circle cx="{source_x}" cy="{y}" r="2.6" fill="{"#3D8585" if reply else "#447DA5"}"/>')
        self.edge(origin, target, [(source_x, y), (target_x, y)], f"{order} {label}", (int((source_x + target_x) / 2), y - 9), "reply" if reply else "main")
        self.messages.append({"scenario": scenario, "order": order, "from": origin, "to": target, "y": y, "label": label, "reply": reply})

    def metadata(self) -> dict[str, object]:
        result = super().metadata()
        result.update({"diagram_type": "sequence", "lifelines": self.lifelines, "messages": self.messages})
        return result

    def write_mermaid(self) -> None:
        lines = ["sequenceDiagram"]
        for lifeline in self.lifelines:
            participant, node = str(lifeline["participant"]), str(lifeline["node"])
            lines.append(f'  participant {participant} as {self.nodes[node]["label"]}')
        keys = [str(item["participant"]) for item in self.lifelines]
        left, right = (keys[0], keys[-1]) if keys else ("U", "P")
        for scenario, group in self.groups.items():
            lines.extend(["  rect rgb(251, 252, 254)", f'    Note over {left},{right}: {group["title"]}'])
            for message in self.messages:
                if message["scenario"] != scenario:
                    continue
                arrow = "-->>" if message["reply"] else "->>"
                lines.append(f'    {message["from"]}{arrow}{message["to"]}: {message["order"]} {message["label"]}')
            note = self.notes.get(scenario)
            if note:
                lines.append(f'    Note over {left},{right}: {note}')
            lines.append("  end")
        (ROOT / f"{self.name}.mmd").write_text("\n".join(lines) + "\n", encoding="utf-8")


def seq_positions(count: int) -> list[int]:
    if count == 4:
        return [180, 460, 740, 1020]
    if count == 5:
        return [140, 360, 580, 800, 1020]
    if count == 7:
        return [100, 260, 420, 580, 740, 900, 1060]
    return [120, 310, 500, 690, 880, 1070]


def draw_sequence(name: str, participants: list[tuple[str, str, str, str]], scenarios: list[tuple[str, str, list[tuple[int, str, str, str, bool]], str]], footer: str) -> None:
    positions = seq_positions(len(participants))
    heights = [60 + len(messages) * 24 for _, _, messages, _ in scenarios]
    y = 110
    scenario_layout: list[tuple[str, int, str, list[tuple[int, str, str, str, bool]], str, int]] = []
    for (key, title, messages, note), height in zip(scenarios, heights, strict=True):
        scenario_layout.append((key, y, title, messages, note, height))
        y += height + 20
    total_height = y + 20
    c = SequenceCanvas(name, 1200, total_height)
    for key, top, title, _messages, _note, height in scenario_layout:
        c.scenario(key, top, title, height)
    for (key, title, detail, *_), x in zip(participants, positions, strict=True):
        tone = "external" if key in ("U", "W", "A") and title in ("管理员", "服务经理", "用户", "企业微信") else "active"
        c.participant(key, x, 30, total_height - 30, title, detail, tone)
    for key, top, _title, messages, note, _height in scenario_layout:
        c.scenario_title(key)
        for index, message in enumerate(messages):
            order, origin, target, label, *rest = message
            c.message(key, order, origin, target, top + 55 + index * 24, label, bool(rest and rest[0]))
        c.notes[key] = note
    c.text(46, total_height - 42, footer, 14, color="#536B81")
    c.finish()


# ---------------------------------------------------------------------------
# 架构与模块图
# ---------------------------------------------------------------------------


def system_boundary() -> None:
    c = Canvas("系统组成与边界")
    c.panel("people", 30, 30, 190, 390, "使用方与入口")
    c.panel("platform", 250, 30, 700, 390, "Fluxion Harness · 平台组件")
    c.panel("external", 980, 30, 190, 390, "外部服务")
    c.node("admin", 45, 135, 160, 88, "管理员", ("Console 浏览器",), "external")
    c.node("users", 45, 265, 160, 88, "服务经理", ("企微 / WebChat",), "external")
    c.node("console", 280, 100, 280, 74, "Console Platform", ("定义、授权、资源与审计",))
    c.node("gateway", 610, 100, 310, 74, "IM Gateway", ("身份解析、路由与事件订阅",))
    c.node("runtime", 280, 210, 280, 74, "Agent Runtime", ("无状态对话执行与接续",))
    c.node("worker", 610, 210, 310, 74, "Agent Worker", ("持久任务、定时、批量与交付",))
    c.node("connector", 280, 320, 280, 74, "IM Connector（可选）", ("企微长连接与连接租约",))
    c.node("capability", 610, 320, 310, 74, "共享能力层", ("执行内核、Skill 与平台接入",))
    c.node("models", 995, 100, 160, 74, "模型网关", ("统一模型接入与脱敏", "屏蔽源头供应商"), "external")
    c.node("mcp", 995, 210, 160, 74, "MCP 服务", ("外部工具接口",), "external")
    c.node("business", 995, 320, 160, 74, "业务平台", ("业务数据与操作",), "external")
    c.panel("data", 30, 450, 690, 140, "共享数据与文件")
    c.node("postgres", 50, 505, 200, 62, "PostgreSQL", ("权威状态",), "store")
    c.node("redis", 270, 505, 200, 62, "Redis", ("缓存、通道流与短租约",), "store")
    c.node("pvc", 490, 505, 210, 62, "共享存储", ("Skill 制品 / 工作区",), "store")
    c.finish()


def logical_layer_nodes(c: Canvas) -> None:
    for key, y, title, detail in (
        ("entry", 30, "1 用户入口", "发送消息、接收结果"),
        ("access", 140, "2 接入层", "连接、身份与消息转交"),
        ("execution", 275, "3 执行层", "对话与后台任务"),
        ("capabilities", 410, "4 能力层", "执行时按需调用"),
        ("data", 545, "5 数据层", "各层按职责读写"),
    ):
        c.panel(key, 30, y, 920, 110 if key != "entry" else 90, title)
        c.text(46, y + 55, detail, 14, color="#64798D")
    c.node("wecom", 220, 50, 190, 60, "企业微信", tone="external")
    c.node("webchat", 500, 50, 200, 60, "WebChat", tone="external")
    c.node("connector", 220, 170, 190, 65, "IM Connector", ("维护企微长连接",))
    c.node("gateway", 500, 170, 200, 65, "IM Gateway", ("识别用户、转交请求",))
    c.node("runtime", 500, 305, 200, 65, "Agent Runtime", ("处理本轮对话",))
    c.node("worker", 790, 305, 155, 65, "Agent Worker", ("后台、定时、批量",))
    c.node("tools", 220, 445, 215, 65, "Skill / MCP 工具", ("完成具体业务操作",))
    c.node("models", 500, 445, 200, 65, "模型网关（外部）", ("统一接入、脱敏与还原",), "external")
    c.node("platforms", 750, 445, 195, 65, "平台接入与登录", ("凭据复用、Session 管理",))
    c.node("postgres", 220, 580, 215, 60, "PostgreSQL", ("配置、会话、执行记录",), "store")
    c.node("redis", 500, 580, 200, 60, "Redis", ("缓存、消息流、短租约",), "store")
    c.node("files", 750, 580, 195, 60, "共享文件", ("技能包、附件、工作区",), "store")
    c.node("admin", 995, 50, 160, 60, "管理员", tone="external")
    c.panel("management", 975, 140, 195, 515, "管理与配置")
    c.node("console", 995, 185, 160, 90, "Console", ("Agent / 授权 / 模型", "凭据 / 运行记录"))
    for y, line in ((330, "提供定义与授权"), (360, "管理模型和凭据"), (390, "查询执行与审计"), (465, "通过内部接口供"), (490, "接入层、执行层使用")):
        c.text(993, y, line, 14, color="#536B81")


def logical_layers() -> None:
    c = Canvas("逻辑分层", 1200, 680)
    logical_layer_nodes(c)
    c.edge("wecom", "connector", [(315, 110), (315, 170)])
    c.edge("webchat", "gateway", [(600, 110), (600, 170)])
    c.edge("connector", "gateway", [(410, 202), (500, 202)], "消息", (455, 190))
    c.edge("gateway", "runtime", [(600, 235), (600, 305)], "发起请求", (647, 275))
    c.edge("runtime", "worker", [(700, 325), (790, 325)], "后台任务", (745, 313))
    c.edge("worker", "runtime", [(790, 350), (700, 350)], "结果回流", (745, 373), "return")
    c.edge("worker", "gateway", [(867, 305), (867, 202), (700, 202)], "独立通知", (804, 190), "async")
    c.edge("runtime", "capabilities", [(600, 370), (600, 410)], "按需调用", (650, 403))
    c.edge("worker", "capabilities", [(867, 370), (867, 410)])
    c.edge("admin", "console", [(1075, 110), (1075, 185)])
    c.finish()


def module_dependency() -> None:
    c = Canvas("模块依赖关系", 1200, 720)
    c.panel("entry", 30, 30, 1140, 80, "用户入口")
    c.node("admin", 130, 45, 160, 55, "管理员", ("Console 浏览器",), "external")
    c.node("wecom", 480, 45, 180, 55, "企业微信", ("IM 入口",), "external")
    c.node("webchat", 880, 45, 180, 55, "WebChat", ("浏览器入口",), "external")
    c.panel("management", 30, 140, 240, 300, "管理面")
    c.node("console", 55, 230, 190, 90, "Console Platform", ("定义、授权、发布", "运行与审计查询"))
    c.panel("access", 300, 140, 260, 300, "接入面")
    c.node("gateway", 325, 190, 210, 65, "IM Gateway", ("无状态请求面",))
    c.node("connector", 325, 310, 210, 65, "IM Connector", ("有状态连接角色",))
    c.panel("execution", 590, 140, 300, 300, "执行面")
    c.node("runtime", 615, 190, 250, 65, "Agent Runtime", ("实时执行与接续",))
    c.node("worker", 615, 310, 250, 65, "Agent Worker", ("异步任务与投递",))
    c.panel("shared", 920, 140, 250, 300, "共享能力层")
    c.node("shared_core", 945, 170, 200, 55, "执行内核", ("运行循环与上下文",))
    c.node("shared_tools", 945, 240, 200, 55, "工具、MCP 与 Skill", ("统一注册与执行",))
    c.node("shared_contracts", 945, 310, 200, 55, "契约与公共支撑", ("接口、错误码、公共库",))
    c.node("modelpool", 945, 380, 200, 55, "模型池", ("成员、凭据与容量",))
    c.panel("data", 30, 460, 1140, 120, "数据与外部依赖")
    c.node("postgres", 60, 500, 200, 55, "PostgreSQL", ("权威状态",), "store")
    c.node("redis", 300, 500, 200, 55, "Redis", ("缓存、流、短租约",), "store")
    c.node("storage", 540, 500, 200, 55, "共享存储", ("制品与工作区",), "store")
    c.node("external", 780, 500, 300, 55, "外部模型网关", ("统一模型接入、脱敏与屏蔽",), "external")
    c.edge("admin", "console", [(210, 100), (150, 230)], "管理", (235, 165))
    c.edge("wecom", "connector", [(570, 100), (570, 125), (290, 125), (290, 342), (325, 342)], "消息", (430, 113))
    c.edge("webchat", "gateway", [(970, 100), (970, 135), (535, 135), (535, 190)], "消息", (750, 123))
    c.edge("connector", "gateway", [(430, 310), (430, 255)], "事件", (480, 283), "async", True)
    c.edge("gateway", "runtime", [(535, 222), (615, 222)], "提交 Run", (575, 210))
    c.edge("runtime", "worker", [(740, 255), (740, 310)], "任务/结果", (795, 283), both=True)
    c.edge("runtime", "shared_core", [(865, 210), (945, 197)], "调用执行内核", (900, 188))
    c.edge("worker", "shared_tools", [(865, 342), (945, 267)], "调用工具/Skill", (900, 300), "control")
    c.edge("modelpool", "external", [(1045, 435), (1045, 500)], "模型调用", (1090, 470), "control")
    c.edge("console", "postgres", [(150, 320), (150, 500)], "配置与审计", (210, 410), "control")
    c.finish()

def deployment() -> None:
    c = Canvas("部署拓扑", 1200, 750)
    c.panel("cluster", 245, 35, 925, 435, "Kubernetes 集群 · 入口代理与业务工作负载")
    c.node("browser", 30, 130, 180, 70, "浏览器", ("Console / WebChat",), "external")
    c.node("wecom", 30, 335, 180, 74, "企业微信", ("官方长连接协议",), "external")
    c.node("nginx", 280, 130, 170, 70, "Nginx", ("Pod / Service · 入口",))
    c.node("gateway", 500, 130, 170, 70, "IM Gateway", ("无状态请求面",))
    c.node("runtime", 750, 130, 180, 70, "Agent Runtime", ("多副本 / 无状态",))
    c.node("worker", 1000, 130, 150, 70, "Agent Worker", ("多副本 / 任务领取",))
    c.node("console", 280, 275, 170, 70, "Console Platform", ("API + 页面资源",))
    c.node("connector", 500, 275, 170, 70, "IM Connector", ("可选 / 多副本",))
    c.edge("browser", "nginx", [(210, 165), (280, 165)], "HTTPS", (245, 153))
    c.edge("nginx", "gateway", [(450, 165), (500, 165)], "聊天", (475, 153))
    c.edge("nginx", "console", [(365, 200), (365, 275)], "管理 / 认证", (422, 242))
    c.edge("gateway", "runtime", [(670, 165), (750, 165)], "Run", (710, 153))
    c.edge("runtime", "worker", [(930, 150), (1000, 150)], "Task", (965, 138))
    c.edge("worker", "runtime", [(1000, 180), (930, 180)], "结果", (965, 199), "return")
    c.edge("worker", "gateway", [(1075, 130), (1075, 88), (585, 88), (585, 130)], "独立投递", (830, 77), "async")
    c.edge("wecom", "connector", [(210, 372), (585, 372), (585, 345)], "WSS", (350, 360), both=True)
    c.edge("connector", "gateway", [(555, 275), (555, 200)], "事件流", (610, 240), "async", True)
    c.panel("data-services", 245, 480, 925, 250, "由环境提供的存储与数据服务")
    c.node("artifact-pvc", 280, 520, 400, 70, "制品文件存储", ("Skill 包 / 附件 / 结果", "挂载：Console / Gateway / Runtime / Worker / Connector"), "store")
    c.node("workspace-pvc", 710, 520, 440, 70, "工作区文件存储", ("草稿 / 执行输出", "挂载：Console / Runtime / Worker"), "store")
    c.node("postgres", 280, 610, 400, 70, "PostgreSQL", ("Console / Runtime / Worker",), "store")
    c.node("redis", 710, 610, 440, 70, "Redis", ("各运行角色的缓存、会话和通道流",), "store")
    c.finish()

def network_capacity() -> None:
    c = Canvas("网络拓扑与容量规划")
    c.panel("users", 30, 30, 240, 420, "用户与入口")
    c.node("wecom", 55, 100, 190, 70, "企业微信", ("WSS 长连接",), "external")
    c.node("browser", 55, 300, 190, 70, "浏览器", ("HTTPS / SSE",), "external")
    c.panel("edge", 300, 30, 230, 420, "入口代理")
    c.node("nginx", 325, 150, 180, 100, "Nginx", ("只服务浏览器入口", "路径分流", "请求体上限"))
    c.panel("cluster", 560, 30, 610, 420, "Kubernetes 集群")
    c.node("gateway", 585, 100, 170, 70, "IM Gateway", ("多副本 / SSE",))
    c.node("connector", 800, 100, 170, 70, "IM Connector", ("按 bot 租约分片",))
    c.node("console", 585, 230, 170, 70, "Console", ("单 Pod",))
    c.node("runtime", 800, 230, 170, 70, "Agent Runtime", ("多副本 / 按并发",))
    c.node("worker", 1010, 230, 140, 70, "Agent Worker", ("多副本 / 按队列",))
    c.panel("external", 30, 480, 1140, 120, "外部依赖与数据服务")
    c.node("pg", 60, 520, 160, 55, "PostgreSQL", ("权威状态",), "store")
    c.node("redis", 260, 520, 160, 55, "Redis", ("缓存与流",), "store")
    c.node("storage", 460, 520, 160, 55, "共享存储", ("制品与工作区",), "store")
    c.node("models", 660, 520, 160, 55, "外部模型网关", ("模型接口",), "external")
    c.node("platforms", 860, 520, 160, 55, "业务平台", ("平台接口",), "external")
    c.edge("browser", "nginx", [(245, 335), (285, 335), (285, 200), (325, 200)], "HTTPS / SSE", (295, 190))
    c.edge("nginx", "gateway", [(505, 185), (585, 135)], "聊天 / 事件", (545, 145))
    c.edge("nginx", "console", [(505, 225), (585, 265)], "管理 / 认证", (545, 260))
    c.edge("wecom", "connector", [(150, 100), (150, 80), (800, 80), (800, 100)], "WSS 直连 Connector", (475, 68))
    c.edge("connector", "gateway", [(800, 135), (755, 135)], "事件流", (777, 123), "async", True)
    c.edge("gateway", "runtime", [(755, 150), (800, 265)], "内网 HTTP", (830, 185))
    c.edge("runtime", "worker", [(970, 265), (1010, 265)], "任务", (990, 253))
    c.edge("runtime", "models", [(885, 300), (885, 455), (740, 455), (740, 520)], "模型调用", (810, 443), "control")
    c.edge("worker", "platforms", [(1080, 300), (1080, 472), (940, 472), (940, 520)], "平台调用", (1010, 460), "control")
    c.text(46, 612, "Nginx 只服务浏览器入口；企业微信 WSS 直连 Connector；MCP 等工具服务经平台接入出站，不单列网络节点。", 14, color="#536B81")
    c.finish()


# ---------------------------------------------------------------------------
# 关键流程时序图
# ---------------------------------------------------------------------------


def flow_binding() -> None:
    participants = [
        ("A", "管理员", "配置资源", ""),
        ("C", "Console", "定义、授权与绑定码", ""),
        ("U", "服务经理", "企微用户", ""),
        ("W", "企业微信", "消息入口", ""),
        ("N", "Connector", "长连接与规范化", ""),
        ("G", "Gateway", "接入、路由与回复", ""),
    ]
    messages = [
        (1, "A", "C", "为服务经理生成绑定码"),
        (2, "C", "A", "返回绑定码 7F3K9Q", True),
        (3, "A", "U", "私下发送绑定码"),
        (4, "U", "W", "发送 /bind 7F3K9Q"),
        (5, "W", "N", "转发绑定消息"),
        (6, "N", "G", "提交绑定事件"),
        (7, "G", "C", "校验绑定码与租户"),
        (8, "C", "G", "登记服务经理身份", True),
        (9, "G", "N", "返回绑定成功"),
        (10, "N", "W", "发送绑定成功"),
        (11, "W", "U", "显示绑定成功", True),
    ]
    draw_sequence("开通与绑定", participants, [("bind", "绑定流程｜管理员为服务经理生成绑定码", messages, "绑定码由管理员线下发给服务经理；绑定不经模型，Console 负责校验与登记。")], "管理员在 Console 为服务经理生成一次性绑定码；服务经理在企微发送后完成身份映射。")

def flow_conversation() -> None:
    participants = [
        ("U", "用户", "企微 / WebChat", ""),
        ("G", "Gateway", "接入、去重与回复", ""),
        ("A", "Runtime 甲", "受理与执行", ""),
        ("B", "Runtime 乙", "下一轮受理与执行", ""),
        ("P", "共享存储", "Run、快照与事件", ""),
        ("M", "外部模型网关", "纯模型调用", ""),
    ]
    first = [
        (1, "U", "G", "问这条告警是什么意思"),
        (2, "G", "A", "校验身份后提交 Run"),
        (3, "A", "P", "保存会话与快照"),
        (4, "A", "P", "领取执行权"),
        (5, "P", "A", "返回会话历史", True),
        (6, "A", "M", "请求模型解释"),
        (7, "M", "A", "返回解释", True),
        (8, "A", "P", "写入回答事件"),
        (9, "A", "G", "SSE 推送解释", True),
        (10, "G", "U", "显示解释", True),
        (11, "U", "G", "断网后重连"),
        (12, "G", "P", "读取确认序号之后"),
        (13, "P", "G", "返回持久事件", True),
        (14, "G", "U", "继续显示解释", True),
    ]
    second = [
        (15, "U", "G", "追问 80 端口"),
        (16, "G", "B", "校验后提交新 Run"),
        (17, "B", "M", "请求模型回答追问"),
        (18, "M", "B", "返回追问回答", True),
        (19, "B", "G", "SSE 推送追问结果", True),
        (20, "G", "U", "显示追问结果", True),
    ]
    draw_sequence(
        "对话请求生命周期",
        participants,
        [
            ("round_one", "第一轮｜解释告警含义（纯模型对话）", first, "本流程只调用模型，不调用 Skill、MCP 或工具；工具类请求见同步 Skill 执行。"),
            ("round_two", "第二轮｜断网重连后追问 80 端口", second, "断线不取消 Run；重连按持久序号回放，不重建 Run；下一轮可以落到另一实例。"),
        ],
        "WebChat 与企微共用同一 Run 生命周期；入口差异（Token/Cookie 与 /bind、Connector）见正文入口说明。",
    )

def flow_sync_skill() -> None:
    participants = [
        ("U", "用户", "发起请求", ""),
        ("G", "Gateway", "接入与回复", ""),
        ("A", "Runtime", "本轮执行", ""),
        ("S", "沙箱", "隔离执行", ""),
        ("P", "制品存储", "不可变 Skill 制品", ""),
        ("M", "模型网关", "模型选择与摘要", ""),
        ("B", "平台接入", "调用业务平台接口", ""),
    ]
    messages = [
        (1, "U", "G", "问客户 A 昨天的告警"),
        (2, "G", "A", "提交告警查询 Run"),
        (3, "A", "M", "请求模型选择 Skill"),
        (4, "M", "A", "选择告警查询 Skill", True),
        (5, "A", "P", "加载告警查询 Skill"),
        (6, "P", "A", "返回 Skill 制品", True),
        (7, "A", "S", "沙箱执行 Skill"),
        (8, "S", "B", "调用外部平台接口"),
        (9, "B", "S", "返回业务数据", True),
        (10, "S", "A", "返回告警结果", True),
        (11, "A", "P", "保存告警结果"),
        (12, "A", "M", "请求模型生成摘要"),
        (13, "M", "A", "返回摘要", True),
        (14, "A", "G", "SSE 推送告警摘要", True),
        (15, "G", "U", "显示告警摘要", True),
    ]
    draw_sequence("同步Skill执行", participants, [("sync", "同步 Skill 执行｜查询客户 A 昨天的告警", messages, "沙箱内 Skill 经平台接入调用业务平台获取业务数据；模型选择与摘要走模型网关；MCP/内置工具同属工具执行。")], "Skill 在当前 Run 内执行；制品只读；工具结果持久化后再交给模型。")

def flow_async_task() -> None:
    participants = [
        ("U", "用户", "发起请求", ""),
        ("G", "Gateway", "接入与回复", ""),
        ("A", "Runtime", "受理与接续", ""),
        ("W", "Worker", "异步执行", ""),
        ("P", "共享存储", "任务、终态与结果", ""),
        ("B", "平台接入", "调用业务平台接口", ""),
        ("M", "模型网关", "生成摘要", ""),
    ]
    join = [
        (1, "U", "G", "导出客户 A 近 30 天日报"),
        (2, "G", "A", "提交日报导出 Run"),
        (3, "A", "P", "保存 Run 与快照"),
        (4, "A", "W", "提交异步任务（JOIN）"),
        (5, "W", "P", "持久化任务受理"),
        (6, "W", "P", "领取日报任务"),
        (7, "W", "B", "调用平台接口取业务数据"),
        (8, "B", "W", "返回业务数据", True),
        (9, "W", "M", "请求模型生成摘要"),
        (10, "M", "W", "返回摘要", True),
        (11, "W", "P", "保存终态与日报"),
        (12, "W", "A", "回传日报结果"),
        (13, "A", "P", "续接原 Run"),
        (14, "A", "G", "SSE 推送日报", True),
        (15, "G", "U", "显示日报结果", True),
    ]
    detach = [
        (16, "U", "G", "导出日报并稍后通知"),
        (17, "G", "A", "提交日报 Run"),
        (18, "A", "W", "提交异步任务（DETACH）"),
        (19, "W", "P", "持久化任务受理"),
        (20, "W", "B", "调用平台接口取业务数据"),
        (21, "B", "W", "返回业务数据", True),
        (22, "W", "M", "请求模型生成摘要"),
        (23, "M", "W", "返回摘要", True),
        (24, "W", "P", "保存终态与日报"),
        (25, "W", "G", "按入口能力投递日报"),
        (26, "G", "U", "推送日报通知", True),
    ]
    draw_sequence(
        "异步任务与结果回流",
        participants,
        [
            ("join", "JOIN｜结果回流当前 Run", join, "Worker 先经平台接入取业务数据，再经模型网关生成摘要；结果回传 Runtime 续接原 Run。"),
            ("detach", "DETACH｜独立执行并推送", detach, "DETACH 独立完成；只有入口支持 PUSH 时才投递，WebChat 结果留在会话或任务中心。"),
        ],
        "提交回执只表示受理；结果先持久化再回流或投递；Worker 故障按租约回收后继续。",
    )

def flow_schedule_batch() -> None:
    participants = [
        ("U", "用户/管理员", "发起与接收", ""),
        ("A", "Runtime", "受理与计划", ""),
        ("W", "Worker", "调度与执行", ""),
        ("P", "共享存储", "计划、任务与结果", ""),
        ("B", "平台接入", "调用业务平台接口", ""),
        ("M", "模型网关", "生成摘要", ""),
        ("G", "Gateway", "结果通知", ""),
    ]
    schedule_messages = [
        (1, "U", "A", "设置每天 9 点导出日报"),
        (2, "A", "P", "保存定时计划"),
        (3, "W", "P", "到点领取日报计划"),
        (4, "W", "P", "复核授权与 Skill 版本"),
        (5, "W", "P", "创建日报任务"),
        (6, "W", "B", "调用平台接口取业务数据"),
        (7, "B", "W", "返回业务数据", True),
        (8, "W", "M", "请求模型生成摘要"),
        (9, "M", "W", "返回摘要", True),
        (10, "W", "P", "保存日报终态"),
        (11, "W", "G", "入口支持 PUSH 时投递"),
        (12, "G", "U", "到点后推送日报", True),
    ]
    batch = [
        (13, "U", "A", "同时检查客户 A/B/C"),
        (14, "A", "W", "创建三个子任务"),
        (15, "W", "P", "持久化任务树"),
        (16, "W", "B", "并行调用平台接口"),
        (17, "B", "W", "返回三家业务数据", True),
        (18, "W", "M", "请求模型汇总"),
        (19, "M", "W", "返回汇总摘要", True),
        (20, "W", "P", "汇总三份结果"),
        (21, "W", "G", "入口支持 PUSH 时投递"),
        (22, "G", "U", "显示三份结果", True),
    ]
    draw_sequence(
        "定时与批量任务",
        participants,
        [
            ("schedule", "定时任务｜每天 9 点导出日报", schedule_messages, "Worker 执行 Skill 时先经平台接入取业务数据，再经模型网关生成摘要；入口支持 PUSH 时才投递。"),
            ("batch", "批量任务｜同时检查客户 A/B/C", batch, "批量用父子任务扇出与汇总；子任务同样经平台接入和模型网关。"),
        ],
        "定时与批量最终都由 Worker 执行；WebChat 结果留在会话，企微等 PUSH 通道自动投递。",
    )

def flow_cancel_resume() -> None:
    participants = [
        ("U", "用户", "发起停止或补充", ""),
        ("G", "Gateway", "接入与回复", ""),
        ("A", "Runtime", "取消与恢复", ""),
        ("W", "Worker", "任务执行", ""),
        ("P", "共享存储", "状态、快照与结果", ""),
    ]
    messages = [
        (1, "U", "G", "点击停止 / 发送 /stop"),
        (2, "G", "A", "转发停止请求"),
        (3, "A", "P", "记录取消意图"),
        (4, "A", "W", "取消未受理的异步提交"),
        (5, "A", "P", "取消未结束的等待"),
        (6, "A", "G", "返回已取消"),
        (7, "G", "U", "确认当前请求已停止", True),
        (8, "U", "G", "补充：只看最近 7 天"),
        (9, "G", "A", "请求恢复"),
        (10, "A", "P", "重新排队并领取"),
        (11, "P", "A", "返回原快照", True),
        (12, "A", "G", "SSE 推送新结果", True),
        (13, "G", "U", "显示最近 7 天结果", True),
    ]
    draw_sequence("取消当前请求与恢复", participants, [("cancel", "取消当前请求｜分析中停止并补充条件", messages, "这是 Run 级取消，入口可以是 WebChat 停止按钮或 IM /stop；不是定时任务取消。")], "已受理的独立任务继续执行；外部动作能否撤回由外部系统决定；恢复沿用原快照和剩余预算。")

def flow_skill_import() -> None:
    participants = [
        ("U", "服务经理", "自建与提交", ""),
        ("A", "管理员", "审批与导入", ""),
        ("C", "Console", "校验、发布与审批", ""),
        ("P", "共享存储", "不可变制品", ""),
        ("R", "Runtime/Worker", "后续执行", ""),
    ]
    submit = [
        (1, "U", "C", "写端口检查 Skill 草稿"),
        (2, "C", "P", "保存草稿"),
        (3, "U", "C", "在草稿沙箱测试"),
        (4, "C", "U", "返回测试结果", True),
        (5, "U", "C", "上传草稿并提交"),
        (6, "C", "P", "校验并写待审制品"),
        (7, "C", "U", "返回待审批", True),
    ]
    approve = [
        (8, "A", "C", "打开待审批 Skill"),
        (9, "C", "P", "读取待审制品"),
        (10, "A", "C", "审批通过"),
        (11, "C", "P", "更新当前版本"),
        (12, "C", "A", "返回已生效", True),
    ]
    admin_import = [
        (13, "A", "C", "上传端口检查 Skill"),
        (14, "C", "P", "校验并写制品"),
        (15, "C", "P", "直接发布当前版本"),
        (16, "C", "A", "返回已生效（默认生效）", True),
        (17, "R", "P", "加载新版本"),
        (18, "P", "R", "返回制品", True),
    ]
    draw_sequence(
        "Skill导入与自建",
        participants,
        [
            ("submit", "服务经理自建并提交", submit, "服务经理在 Console 写草稿、沙箱测试、上传后进入待审批。"),
            ("approve", "管理员审批", approve, "管理员就是 Console 用户，不是独立审批节点；审批通过后更新当前版本。"),
            ("admin_import", "管理员直接导入（默认生效）", admin_import, "管理员在 Console 直接上传并发布，默认生效，不再进入审批。"),
        ],
        "制品不可变；服务经理自建需审批；管理员直接导入默认生效；新版本只影响后续执行。",
    )

# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def state_node(c: Canvas, key: str, x: int, y: int, title: str, tone: str = "active", w: int = 160, group: str | None = None, lines: tuple[str, ...] = (), step: str = "") -> None:
    c.node(key, x, y, w, 60, title, lines, tone, True)
    if group:
        c.nodes[key]["group"] = group
    if step:
        number_badge(c, key, step)


def run_states() -> None:
    c = Canvas("请求执行状态机", 1000, 470)
    c.panel("ACTIVE_RUN", 30, 40, 725, 320, "未结束的请求 · 均可能失败或取消")
    state_node(c, "CREATED", 50, 150, "已创建", group="ACTIVE_RUN", lines=("待领取",), step="1")
    state_node(c, "RUNNING", 300, 150, "执行中", group="ACTIVE_RUN", step="2")
    state_node(c, "WAITING_TOOL", 550, 70, "等待工具结果", w=180, group="ACTIVE_RUN", step="A")
    state_node(c, "WAITING_INPUT", 550, 250, "等待用户输入", w=180, group="ACTIVE_RUN", step="B")
    state_node(c, "COMPLETED", 810, 150, "已完成", "success", step="3")
    state_node(c, "FAILED", 810, 65, "失败", "failure")
    state_node(c, "CANCELLED", 810, 250, "已取消", "inactive")
    c.edge("CREATED", "RUNNING", [(210, 180), (300, 180)], "领取", (255, 168))
    c.edge("RUNNING", "WAITING_TOOL", [(335, 150), (335, 90), (550, 90)], "A1 等待后台结果", (440, 78))
    c.edge("WAITING_TOOL", "RUNNING", [(640, 130), (640, 140), (410, 140), (410, 150)], "A2 结果就绪", (495, 135), "return")
    c.edge("RUNNING", "WAITING_INPUT", [(335, 210), (335, 280), (550, 280)], "B1 需要确认或补充", (440, 268))
    c.edge("WAITING_INPUT", "CREATED", [(640, 310), (640, 340), (130, 340), (130, 210)], "B2 有效输入后重新排队", (385, 334), "return")
    c.edge("RUNNING", "COMPLETED", [(460, 180), (810, 180)], "处理完成", (635, 168))
    c.edge("ACTIVE_RUN", "FAILED", [(755, 95), (810, 95)], "失败", (782, 83))
    c.edge("ACTIVE_RUN", "CANCELLED", [(755, 280), (810, 280)], "取消", (782, 268))
    reading_guide(c, "通常顺序：1 已创建 → 2 执行中 → 3 已完成。", "A1 → A2：等工具后回到 2；B1 → B2：等用户后回到 1，仍是同一次请求。", 418)
    c.finish()


def task_states() -> None:
    c = Canvas("后台任务状态机", 1000, 510)
    c.panel("ACTIVE_TASK", 30, 40, 735, 350, "未结束的任务 · 均可能完成、失败或取消")
    state_node(c, "QUEUED", 55, 170, "排队中", group="ACTIVE_TASK", step="1")
    state_node(c, "RUNNING", 300, 170, "执行中", group="ACTIVE_TASK", step="2")
    state_node(c, "WAITING_EXTERNAL", 550, 80, "等待外部结果", w=180, group="ACTIVE_TASK", step="A")
    state_node(c, "WAITING_HUMAN", 550, 270, "等待人工处理", w=180, group="ACTIVE_TASK", step="B")
    state_node(c, "COMPLETED", 810, 90, "已完成", "success", step="3")
    state_node(c, "FAILED", 810, 190, "失败", "failure")
    state_node(c, "CANCELLED", 810, 290, "已取消", "inactive")
    c.edge("QUEUED", "RUNNING", [(215, 200), (300, 200)], "领取", (258, 188))
    c.edge("RUNNING", "WAITING_EXTERNAL", [(335, 170), (335, 100), (550, 100)], "A1 等待外部结果", (440, 88))
    c.edge("WAITING_EXTERNAL", "RUNNING", [(640, 140), (640, 150), (410, 150), (410, 170)], "A2 结果就绪", (495, 145), "return")
    c.edge("RUNNING", "WAITING_HUMAN", [(335, 230), (335, 300), (550, 300)], "B1 等待人工处理", (440, 288))
    c.edge("WAITING_HUMAN", "RUNNING", [(640, 330), (640, 360), (410, 360), (410, 230)], "B2 处理完成", (495, 354), "return")
    c.edge("RUNNING", "QUEUED", [(385, 230), (385, 270), (135, 270), (135, 230)], "C 可重试或回收", (260, 258), "return")
    c.edge("ACTIVE_TASK", "COMPLETED", [(765, 120), (810, 120)], "完成", (788, 108))
    c.edge("ACTIVE_TASK", "FAILED", [(765, 210), (810, 210)], "失败", (788, 198))
    c.edge("ACTIVE_TASK", "CANCELLED", [(765, 300), (810, 300)], "取消", (788, 288))
    reading_guide(c, "通常顺序：1 排队中 → 2 执行中 → 3 已完成。", "A：等待外部结果；B：等待人工处理；C：可重试失败或执行者失约后重新排队。", 418)
    c.finish()


def schedule_states() -> None:
    c = Canvas("定时计划状态机", 1000, 510)
    state_node(c, "ACTIVE", 90, 170, "已启用", step="1")
    state_node(c, "PAUSED", 90, 290, "已暂停", step="A")
    state_node(c, "NEEDS_ATTENTION", 380, 290, "需要人工处理", w=200, step="C")
    state_node(c, "ENDED", 760, 170, "已结束", "success", step="2")
    state_node(c, "CANCELLED", 760, 290, "已取消", "inactive", step="D")
    c.edge("ACTIVE", "PAUSED", [(130, 230), (130, 290)], "A1 暂停", (98, 269))
    c.edge("PAUSED", "ACTIVE", [(210, 290), (210, 230)], "A2 恢复", (253, 269), "return")
    c.edge("ACTIVE", "ENDED", [(250, 200), (760, 200)], "B 一次性完成或周期结束", (505, 188))
    c.edge("ACTIVE", "NEEDS_ATTENTION", [(250, 215), (380, 320)], "C 需要处理", (300, 288))
    c.edge("NEEDS_ATTENTION", "ACTIVE", [(430, 290), (430, 260), (210, 260), (210, 230)], "C2 处理后恢复", (320, 248), "return")
    c.edge("ACTIVE", "CANCELLED", [(250, 230), (520, 230), (520, 360), (760, 360)], "D 取消或删除", (640, 348))
    reading_guide(c, "通常顺序：1 已启用 → 2 已结束；A：暂停与恢复。", "C：需要人工处理；D：取消或删除。已结束与已取消的结束原因分别记录。", 418)
    c.finish()


def delivery_states() -> None:
    c = Canvas("结果投递状态机", 1000, 510)
    c.node("NEEDS_NOTIFICATION", 50, 160, 170, 60, "需要单独通知？", tone="external")
    state_node(c, "PENDING", 350, 160, "待投递", step="1")
    state_node(c, "SENT", 780, 100, "已送达", "success", 170, step="2")
    state_node(c, "FAILED", 780, 270, "投递失败", "failure", 170, step="B")
    state_node(c, "NONE", 350, 295, "无需投递", "inactive")
    c.edge("NEEDS_NOTIFICATION", "PENDING", [(220, 190), (350, 190)], "需要", (285, 178))
    c.edge("NEEDS_NOTIFICATION", "NONE", [(135, 220), (135, 325), (350, 325)], "不需要", (245, 313))
    c.edge("PENDING", "SENT", [(510, 180), (650, 180), (650, 130), (780, 130)], "收到送达确认", (704, 118))
    c.edge("PENDING", "FAILED", [(510, 205), (610, 205), (610, 300), (780, 300)], "B 停止重试", (690, 288))
    c.edge("PENDING", "PENDING", [(430, 160), (430, 90), (550, 90), (550, 140), (490, 140), (490, 160)], "A 发送失败后重试", (490, 78), "return")
    reading_guide(c, "先判断是否单独通知。需要时：1 待投递 → 2 已送达；不需要时为“无需投递”。", "A：暂时失败，等待后再发；B：不能重试或次数用完。没有送达确认，就不能算已送达。", 418)
    c.finish()


def review_states() -> None:
    c = Canvas("Skill制品审核状态机", 1000, 510)
    state_node(c, "PENDING", 90, 170, "待审核", step="1")
    state_node(c, "APPROVED", 750, 70, "已通过", "success", step="2")
    state_node(c, "REJECTED", 750, 170, "已拒绝", "failure", step="A")
    state_node(c, "SUPERSEDED", 750, 280, "已被新版本替代", "inactive", 180, step="B")
    c.edge("PENDING", "APPROVED", [(250, 185), (400, 185), (400, 100), (750, 100)], "审核通过", (575, 88))
    c.edge("PENDING", "REJECTED", [(250, 200), (750, 200)], "A 审核拒绝", (575, 188))
    c.edge("PENDING", "SUPERSEDED", [(250, 215), (400, 215), (400, 310), (750, 310)], "B 提交新的待审核版本", (575, 298))
    reading_guide(c, "通常顺序：1 待审核 → 2 已通过；A：审核拒绝。", "B：新待审版本替代旧待审版本。已通过的版本不会被这条分支改写。", 418)
    c.finish()


def experience_states() -> None:
    c = Canvas("经验状态机", 1000, 540)
    state_node(c, "PROPOSED", 80, 100, "候选", w=170, step="1")
    state_node(c, "TRIAL", 400, 100, "试用中", w=170, step="2")
    state_node(c, "ACTIVE", 400, 260, "已启用", "success", 170, step="3")
    state_node(c, "RETIRED", 750, 260, "已退役", "inactive", 170, step="4")
    c.edge("PROPOSED", "TRIAL", [(250, 130), (400, 130)], "回放验证通过", (325, 118))
    c.edge("TRIAL", "ACTIVE", [(485, 160), (485, 260)], "试用达标", (535, 220))
    c.edge("PROPOSED", "ACTIVE", [(165, 160), (165, 290), (400, 290)], "A 符合启用条件", (280, 278))
    c.edge("ACTIVE", "PROPOSED", [(400, 275), (350, 275), (350, 145), (250, 145)], "B 重新评估", (350, 215), "return")
    c.edge("PROPOSED", "RETIRED", [(165, 100), (165, 35), (835, 35), (835, 260)], "D1 候选退役", (600, 25))
    c.edge("TRIAL", "RETIRED", [(485, 100), (485, 60), (790, 60), (790, 260)], "D2 试用退役", (650, 50))
    c.edge("ACTIVE", "RETIRED", [(570, 290), (750, 290)], "结束使用", (660, 278))
    c.edge("RETIRED", "PROPOSED", [(835, 320), (835, 365), (50, 365), (50, 130), (80, 130)], "C 重新提炼", (450, 355), "return")
    reading_guide(c, "通常顺序：1 候选 → 2 试用中 → 3 已启用 → 4 已退役；只有 3 会用于后续处理。", "A：直接启用；B：退回 1 重新评估；C：重新提炼回到 1；D1 / D2：候选或试用时退役。", 445)
    c.finish()


DRAWINGS = {
    "系统组成与边界": system_boundary,
    "逻辑分层": logical_layers,
    "模块依赖关系": module_dependency,
    "部署拓扑": deployment,
    "网络拓扑与容量规划": network_capacity,
    "开通与绑定": flow_binding,
    "对话请求生命周期": flow_conversation,
    "同步Skill执行": flow_sync_skill,
    "异步任务与结果回流": flow_async_task,
    "定时与批量任务": flow_schedule_batch,
    "取消当前请求与恢复": flow_cancel_resume,
    "Skill导入与自建": flow_skill_import,
}


def main() -> None:
    names = sys.argv[1:] or list(DRAWINGS)
    for name in names:
        if name not in DRAWINGS:
            raise ValueError(f"Unknown figure: {name}")
        DRAWINGS[name]()
        print("written", name)


if __name__ == "__main__":
    main()
