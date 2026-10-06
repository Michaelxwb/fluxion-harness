from muad_agent_core.prompt import DefaultPromptBuilder, PromptBuilder, PromptSkill


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
