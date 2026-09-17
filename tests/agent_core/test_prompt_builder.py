from muad_agent_core.prompt import DefaultPromptBuilder, PromptBuilder, PromptSkill


def _skills() -> tuple[PromptSkill, ...]:
    return (
        PromptSkill(key="policy-check", name="Policy Check", description="checks policy"),
        PromptSkill(
            key="device-audit",
            name="Device Audit",
            description="audits devices",
            platform_label="MSS",
        ),
    )


def test_builder_includes_instructions_and_skill_catalog() -> None:
    builder: PromptBuilder = DefaultPromptBuilder()

    prompt = builder.build(instructions="be helpful", skills=_skills())

    assert prompt.startswith("be helpful")
    assert "- policy-check: Policy Check - checks policy" in prompt
    assert "- device-audit: Device Audit [MSS] - audits devices" in prompt


def test_version_marker_is_optional() -> None:
    without = DefaultPromptBuilder().build(instructions="x", skills=())
    with_version = DefaultPromptBuilder(prompt_template_version="1").build(instructions="x", skills=())

    assert "prompt_template_version" not in without
    assert "<!-- prompt_template_version: 1 -->" in with_version


def test_empty_skill_catalog_is_omitted() -> None:
    prompt = DefaultPromptBuilder().build(instructions="be helpful", skills=())

    assert prompt == "be helpful"
