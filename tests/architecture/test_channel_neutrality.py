"""B-09：通道中立性的静态守卫（核心域零渠道字样 + `channel` 取值无编造）。

不得 Mock 的真实边界：**源码静态检查**——读仓库里的真实源文件、按词元扫描 / 按 AST 取字面量，
被测对象就是源码本身。

B-07（`tests/test_attachment_contract.py`）只钉住了**类型定义处**（`Literal["WECOM"]` 只允许一处）；
本守卫钉的是**取值填充处**：换成第二个渠道时，"核心域里凭空编一个通道名"必须当场变红，而不是等到
有人开始读那个字段（例如按通道做授权）才暴露。

三条断言对应场景的三句：
① `agent-runtime` / `agent-worker` **零**渠道专有字样与取件形状（无例外清单）；
② 核心域构造 `ResolveDefinitionRequest` 时，`channel` **只能是表达式或显式省略**，不能是字面量；
③ 全仓渠道专有字样只允许出现在 `ALLOWED_SURFACES` 列出的面上。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 渠道专有字样：既是渠道名，也是**取件形状**（凭据/媒体标识字段名）。
#: 一律按大小写不敏感匹配——`WECOM` 与 `WeComAdapter` 是同一件事的两种写法。
CHANNEL_TOKENS = (
    "wecom",
    "aeskey",
    "aes_key",
    "url_private",
    "download_code",
    "media_id",
)

#: 允许出现渠道专有字样的**全部**位置（相对仓库根的前缀）。要放宽时改这里并写清理由，
#: 而不是把断言改松——这样"多出来的那处"在 review 里是可见的。
ALLOWED_SURFACES = (
    # 适配器层：渠道差异只活在这里（AD-8 / RULE-07）
    "apps/im-gateway/src/muad_im_gateway/channels/",
    # 装配根：必须挑一个适配器来装配；它只做装配，不按通道分支
    "apps/im-gateway/src/muad_im_gateway/main.py",
    # console 的通道管理面：它的职责就是管理通道账号
    "apps/console-platform/backend/src/muad_console_platform/application/channel_admin_service.py",
    "apps/console-platform/backend/src/muad_console_platform/application/dto.py",
    "apps/console-platform/backend/src/muad_console_platform/infrastructure/models/channel.py",
    # 部署配置面：每个通道的**连接参数**（`WECOM_WS_URL`/`WECOM_WS_CA_FILE`）由适配器读，
    # 放在共享 settings 里是"配置按通道分名字"的正常做法，不是核心域分支
    "packages/common/src/muad_common/settings.py",
    # 类型定义处（B-07 已钉住"只此一处"）
    "packages/contracts/src/muad_contracts/enums.py",
)

CORE_DOMAIN = ("apps/agent-runtime", "apps/agent-worker")


def _source_files(relative: str) -> list[Path]:
    """某个部署单元/包的生产源码（只扫 `src/`，不含 tests 与打包产物）。"""
    return sorted(path for path in (ROOT / relative).rglob("*.py") if "src" in path.parts)


def _all_source_files() -> list[Path]:
    files: list[Path] = []
    for parent in ("apps", "packages"):
        for unit in sorted((ROOT / parent).iterdir()):
            if unit.is_dir():
                files.extend(_source_files(f"{parent}/{unit.name}"))
    return files


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _token_hits(path: Path) -> list[str]:
    """**全文**命中的渠道词元（含注释与文档串）——① 用的就是这个（核心域零容忍）。"""
    lowered = path.read_text(encoding="utf-8").lower()
    return [token for token in CHANNEL_TOKENS if token in lowered]


def _docstring_constants(tree: ast.AST) -> set[int]:
    """所有文档字符串常量节点的 id（模块/类/函数体的第一条 Expr）。"""
    ids: set[int] = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            ids.add(id(first.value))
    return ids


def _code_token_hits(path: Path) -> list[str]:
    """**代码**里命中的渠道词元：标识符 + 关键字参数名 + 非文档字符串的字面量。

    注释与文档串不算——它们常在说明"这个字段必须无处可放"（例如 `AttachmentRef` 的文档串
    逐个点名不许出现哪些凭据），那是说明而不是泄漏；真正的风险是代码里长出按通道的分支。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_constants(tree)
    texts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            texts.append(node.id)
        elif isinstance(node, ast.Attribute):
            texts.append(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            texts.append(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            texts.append(node.value)
    lowered = [text.lower() for text in texts]
    return [token for token in CHANNEL_TOKENS if any(token in text for text in lowered)]


def _allowed(relative: str) -> bool:
    return any(relative.startswith(surface) for surface in ALLOWED_SURFACES)


def _fabricated_channels(path: Path) -> list[str]:
    """核心域里 `ResolveDefinitionRequest(channel=<字面量>)` 的位置（②的机检）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "ResolveDefinitionRequest":
            continue
        for keyword in node.keywords:
            if keyword.arg == "channel" and isinstance(keyword.value, ast.Constant):
                hits.append(f"{_relative(path)}:{node.lineno}")
    return hits


def test_b09_core_domain_carries_no_channel_vocabulary() -> None:
    """① 核心域零渠道字样与取件形状——**无例外清单**：一处都不该有。"""
    offenders = {
        _relative(path): _token_hits(path)
        for unit in CORE_DOMAIN
        for path in _source_files(unit)
        if _token_hits(path)
    }

    assert offenders == {}, (
        "核心域（agent-runtime / agent-worker）不得出现渠道专有字样与取件形状；"
        f"渠道差异只允许留在 im-gateway 的 channels/ 适配器层：{offenders}"
    )


def test_b09_core_domain_never_fabricates_a_channel_value() -> None:
    """② 核心域构造 `ResolveDefinitionRequest` 时 `channel` 只能是表达式或显式省略。

    只看字面量是**故意**的：`channel="WECOM"` 与 `channel="DINGTALK"` 是同一个错误——
    调用点并没有真值，却要凭空编一个；而 `channel=request.channel.type` 是真的有真值。
    """
    fabricated = [hit for unit in CORE_DOMAIN for path in _source_files(unit)
                  for hit in _fabricated_channels(path)]

    assert fabricated == [], (
        "核心域不得给 `ResolveDefinitionRequest.channel` 传字面量：有真值就传真值，"
        f"没有就显式省略（两者都表达得出来）：{fabricated}"
    )


def test_b09_channel_vocabulary_only_lives_on_the_allowed_surfaces() -> None:
    """③ 全仓扫一遍：渠道专有字样只允许出现在适配器层 / 装配根 / console 通道管理面 / 定义处。"""
    offenders = {
        _relative(path): _code_token_hits(path)
        for path in _all_source_files()
        if _code_token_hits(path) and not _allowed(_relative(path))
    }

    assert offenders == {}, (
        "渠道专有字样只能出现在 `ALLOWED_SURFACES`：适配器层 `channels/`、装配根 `main.py`、"
        f"console 通道管理面、`enums.py` 定义处。其余位置出现即为渠道差异泄漏：{offenders}"
    )
