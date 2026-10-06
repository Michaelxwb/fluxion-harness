import dataclasses
import uuid

from muad_agent_core.prompt import (
    CATALOG_TRUNCATED_NOTE,
    MAX_CATALOG_BYTES,
    MAX_CATALOG_ENTRIES,
    SKILL_CATALOG_HEADER,
    SKILL_CATALOG_HINT,
    DefaultPromptBuilder,
    PromptBuilder,
    PromptSkill,
    bounded_catalog,
    prompt_skill,
    render_skill_entry,
)
from muad_contracts import ResolvedSkill


def _skills() -> tuple[PromptSkill, ...]:
    return (
        PromptSkill(key="policy-check", name="Policy Check", description="checks policy"),
        PromptSkill(
            key="device-audit",
            name="Device Audit",
            description="audits devices",
            platform_label="example-platform",
        ),
    )


def test_builder_includes_instructions_and_skill_catalog() -> None:
    builder: PromptBuilder = DefaultPromptBuilder()

    prompt = builder.build(instructions="be helpful", skills=_skills())

    assert prompt.startswith("be helpful")
    assert "- policy-check: Policy Check - checks policy" in prompt
    assert "- device-audit: Device Audit [example-platform] - audits devices" in prompt


def test_version_marker_is_optional() -> None:
    without = DefaultPromptBuilder().build(instructions="x", skills=())
    with_version = DefaultPromptBuilder(prompt_template_version="1").build(instructions="x", skills=())

    assert "prompt_template_version" not in without
    assert "<!-- prompt_template_version: 1 -->" in with_version


def test_empty_skill_catalog_is_omitted() -> None:
    prompt = DefaultPromptBuilder().build(instructions="be helpful", skills=())

    assert prompt == "be helpful"

def test_section_order_is_a_contract() -> None:
    """段落顺序是契约，不只是排版（2026-10-07 借鉴 P10 的做法）。

    顺序一旦只是 `sections.append(...)` 的副产物，任何一次重构都能悄悄挪动它——而"每轮渲染出
    同一份提示"这条保证就建立在它上面。断言**相对位置**，不要断言"包含某段文本"：后者在顺序
    错乱（甚至同一段出现两次）时同样会通过。
    """
    prompt = DefaultPromptBuilder(prompt_template_version="1").build(
        instructions="be helpful", skills=_skills()
    )

    assert (
        prompt.index("be helpful")
        < prompt.index("## Available skills")
        < prompt.index("<!-- prompt_template_version")
    ), prompt


def test_optional_sections_are_appended_not_inserted() -> None:
    """可选段只追加到尾部，前面的相对位置不因它出现而改变。"""
    without = DefaultPromptBuilder().build(instructions="be helpful", skills=())
    with_skills = DefaultPromptBuilder().build(instructions="be helpful", skills=_skills())

    assert with_skills.startswith(without)


def _many(count: int, description: str) -> tuple[PromptSkill, ...]:
    return tuple(
        PromptSkill(key=f"skill-{index:03d}", name="Demo", description=description)
        for index in range(count)
    )


def test_catalog_is_capped_by_entry_count() -> None:
    """条目数上限：超出的是**整条**不进提示词，且丢掉的正是顺序最靠后的那些。"""
    skills = _many(MAX_CATALOG_ENTRIES + 7, "d")

    kept, dropped = bounded_catalog(skills)

    assert len(kept) == MAX_CATALOG_ENTRIES
    assert dropped == skills[MAX_CATALOG_ENTRIES:]
    prompt = DefaultPromptBuilder().build(instructions="be helpful", skills=skills)
    assert render_skill_entry(kept[-1]) in prompt
    assert render_skill_entry(dropped[0]) not in prompt


def test_catalog_is_capped_by_bytes_and_keeps_whole_entries() -> None:
    """字节上限可以先于条目数咬；留下的每一条都是**完整**条目，绝不截半行。"""
    skills = _many(MAX_CATALOG_ENTRIES, "说明" * 30)

    kept, dropped = bounded_catalog(skills)

    assert dropped, "构造的数据必须真的超字节上限，否则这条用例空转"
    assert len(kept) < MAX_CATALOG_ENTRIES
    used = sum(len(render_skill_entry(skill).encode("utf-8")) + 1 for skill in kept)
    assert used <= MAX_CATALOG_BYTES
    # 再多收一条就越界 —— 恰好停在边界上，不多留也不少留
    assert used + len(render_skill_entry(dropped[0]).encode("utf-8")) + 1 > MAX_CATALOG_BYTES
    prompt = DefaultPromptBuilder().build(instructions="be helpful", skills=skills)
    assert all(render_skill_entry(skill) in prompt for skill in kept)


def test_truncation_is_announced_only_when_something_was_dropped() -> None:
    """被截断是**模型行为会变**的那种缺失（它会以为清单就是全部），必须说出来；
    没被截断时一个字都不加——那只是噪音。这一行也是 `search_skills` 的发现路径。"""
    intact = DefaultPromptBuilder().build(instructions="be helpful", skills=_many(3, "d"))
    truncated = DefaultPromptBuilder().build(
        instructions="be helpful", skills=_many(MAX_CATALOG_ENTRIES + 1, "d")
    )

    assert CATALOG_TRUNCATED_NOTE not in intact
    assert CATALOG_TRUNCATED_NOTE in truncated


def test_a_catalog_within_budget_renders_exactly_the_projected_fields() -> None:
    """没超限时目录**逐字**由「标题 + 路由提示 + 条目」构成。

    用 `==` 而不是 `in`：正文一旦被塞进目录（两级加载退化成"启动就塞满提示词"），只有精确比对
    才拦得住——而那正是这套设计要防的事，且它会落在**受保护前缀**里永远压不掉。
    """
    skills = _skills()

    kept, dropped = bounded_catalog(skills)

    assert (kept, dropped) == (skills, ())
    assert DefaultPromptBuilder().build(instructions="be helpful", skills=skills) == "\n\n".join(
        [
            "be helpful",
            "\n".join(
                [
                    SKILL_CATALOG_HEADER,
                    SKILL_CATALOG_HINT,
                    *(render_skill_entry(skill) for skill in skills),
                ]
            ),
        ]
    )


def test_prompt_projection_keeps_no_artifact_details() -> None:
    """目录项只带 key / name / description(+platform_label)：产物细节一个字都不进提示词。"""
    resolved = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="policy-check",
        name="Policy Check",
        description="checks policy",
        version="9.9.9",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/policy-check/9.9.9/skill.zip",
        frontmatter={"platform_label": "Demo"},
    )

    projected = prompt_skill(resolved)

    assert {field.name for field in dataclasses.fields(projected)} == {
        "key",
        "name",
        "description",
        "platform_label",
    }
    rendered = DefaultPromptBuilder().build(instructions="be helpful", skills=(projected,))
    assert resolved.checksum not in rendered
    assert resolved.storage_key not in rendered
    assert resolved.version not in rendered
    assert str(resolved.artifact_id) not in rendered
    assert projected.key in rendered and projected.name in rendered
