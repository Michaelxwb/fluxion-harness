from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from muad_contracts import ResolvedSkill

SKILL_CATALOG_HEADER = "## Available skills"
# 目录的**路由提示**：目录只负责让模型知道"有哪些、各管什么"，何时去读正文是它自己的判断。
# 少了这句，目录看起来像一份必须照做的清单，而不是一份可按需打开的索引。
SKILL_CATALOG_HINT = "Load one with load_skill only when its instructions are relevant."
# 目录被截断时才追加的一行。**只在真的丢了条目时出现**——`harness-arch` 的判据是「段落的缺失
# 会不会改变模型的行为」：清单被截断会让模型以为那就是全部技能，属于会改变行为的那种缺失，
# 必须显式说明；而清单完整时写「还有别的」只是噪音。它同时是 `search_skills` 的发现路径。
CATALOG_TRUNCATED_NOTE = "- (more skills are available; use search_skills to find them by keyword)"

# 目录的双上限（条目数 + UTF-8 字节，边界为**严格大于**，同 `harness-skill` 的阈值规则）。
# 目录是上下文里**唯一只增不减**的部分：系统提示落在压缩的受保护前缀里，任何层都不动它，
# 所以它一旦变大就没有机制能收回来——上限只有在这里设才有意义。
# 100 / 8 KiB 是「够用且不失控」的取值：按中文描述实测约 146 B/条，字节口径先咬（≈54 条）。
# 被丢掉的技能对模型不可见（但仍可被 `search_skills` 找到、也仍可 `load_skill`）；丢掉的是
# 调用方顺序里排在后面的那些，生产里即运营自己排在后面的 `sort_order`。
MAX_CATALOG_ENTRIES = 100
MAX_CATALOG_BYTES = 8000
_ENTRY_SEPARATOR_BYTES = 1  # 条目之间的换行


@dataclass(frozen=True, slots=True)
class PromptSkill:
    key: str
    name: str
    description: str
    platform_label: str | None = None


def prompt_skill(skill: ResolvedSkill) -> PromptSkill:
    """`ResolvedSkill` → 目录条目：**只带名称/描述**，产物细节不进提示词。

    缺了这一步，`DefaultPromptBuilder` 的 `## Available skills` 段永远为空 —— 模型不知道
    自己有哪些技能可按名加载（症状：用户自然语言问「你有哪些技能」时，模型只能答「我无法
    列出，请告诉我 key」）。授权判定仍在上游（生效集合），本函数只做投影。
    """
    label = skill.frontmatter.get("platform_label")
    return PromptSkill(
        key=skill.key,
        name=skill.name,
        description=skill.description,
        platform_label=str(label) if label else None,
    )


def render_skill_entry(skill: PromptSkill) -> str:
    """目录条目的唯一行格式。**提示词与 `search_skills` 的返回值共用它**——两处各写一份的话，
    模型从检索里认出的形状会和目录里的对不上。
    """
    label = f" [{skill.platform_label}]" if skill.platform_label else ""
    return f"- {skill.key}: {skill.name}{label} - {skill.description}"


def bounded_catalog(
    skills: Sequence[PromptSkill],
) -> tuple[tuple[PromptSkill, ...], tuple[PromptSkill, ...]]:
    """按双上限取前缀，返回 `(kept, dropped)`；超限**整条丢弃并停止**，绝不截半行。

    顺序即调用方给的顺序（生产里是 `sort_order, key`），所以「谁常驻」仍是可控的运营决策。
    调用方除了把它交给 `DefaultPromptBuilder`，还应当用返回的 `dropped` 留痕：被丢掉的技能
    模型看不见 key，等于不存在，静默丢弃是这一族里最难查的故障。
    """
    kept: list[PromptSkill] = []
    used_bytes = 0
    for index, skill in enumerate(skills):
        size = len(render_skill_entry(skill).encode("utf-8")) + _ENTRY_SEPARATOR_BYTES
        if len(kept) >= MAX_CATALOG_ENTRIES or used_bytes + size > MAX_CATALOG_BYTES:
            return tuple(kept), tuple(skills[index:])
        kept.append(skill)
        used_bytes += size
    return tuple(kept), ()


class PromptBuilder(Protocol):
    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str: ...


@dataclass(frozen=True, slots=True)
class DefaultPromptBuilder:
    prompt_template_version: str | None = None

    def build(self, *, instructions: str, skills: Sequence[PromptSkill]) -> str:
        """装配系统提示。**目录在这里收口**：这是提示词唯一的装配点，上限放在这里才不会被绕过。

        `instructions` 不在这里截断——截断系统提示等于换了一个 Agent（`harness-arch`：
        降级必须语义等价，否则不许降级），它的上限在保存边界（Console 的 DTO）。
        """
        sections = [instructions.strip()]
        if skills:
            kept, dropped = bounded_catalog(skills)
            lines = [SKILL_CATALOG_HEADER, SKILL_CATALOG_HINT]
            lines.extend(render_skill_entry(skill) for skill in kept)
            if dropped:
                lines.append(CATALOG_TRUNCATED_NOTE)
            sections.append("\n".join(lines))
        if self.prompt_template_version is not None:
            sections.append(f"<!-- prompt_template_version: {self.prompt_template_version} -->")
        return "\n\n".join(sections)
