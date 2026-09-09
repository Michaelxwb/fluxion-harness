"""CMD-01（unit）：CommandParser 解析规则矩阵（设计 §8）。

先写测试记 RED：`fluxion.commands` 包在 TASK-002 实现前不存在，
本文件 collection 即失败，对应"命令解析尚无实现"的预期缺陷。
"""

from __future__ import annotations

from fluxion.commands.parser import parse_command


def test_CMD01_plain_new_is_command() -> None:
    parsed = parse_command("/new")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "new"
    assert parsed.invocation.args == ()
    assert parsed.invocation.remainder == ""


def test_CMD01_leading_whitespace_still_command() -> None:
    parsed = parse_command("   /new")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "new"


def test_CMD01_double_slash_is_literal_message() -> None:
    parsed = parse_command("//new")
    assert parsed.kind == "message"
    assert parsed.literal_text == "/new"


def test_CMD01_command_with_args() -> None:
    parsed = parse_command("/new abc")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "new"
    assert parsed.invocation.args == ("abc",)


def test_CMD01_unknown_name_still_parses_as_command() -> None:
    # 解析只管语法；未知与否由 Dispatcher 判（fail-closed 在执行层）。
    parsed = parse_command("/unknown")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "unknown"


def test_CMD01_slash_mid_text_is_message() -> None:
    parsed = parse_command("hello /new")
    assert parsed.kind == "message"


def test_CMD01_skill_with_remainder_preserved() -> None:
    parsed = parse_command("/skill health-check 你好 世界")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "skill"
    assert parsed.invocation.args[0] == "health-check"
    # remainder 保留原始 spacing，不重新 join
    assert parsed.invocation.remainder == "health-check 你好 世界"


def test_CMD01_bare_skill_is_command_without_args() -> None:
    parsed = parse_command("/skill")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "skill"
    assert parsed.invocation.args == ()


def test_CMD01_uppercase_is_distinct_name() -> None:
    # §8.4：大小写敏感，/NEW 不得归一化为 new（Dispatcher 判 unknown）。
    parsed = parse_command("/NEW")
    assert parsed.kind == "command"
    assert parsed.invocation is not None
    assert parsed.invocation.name == "NEW"


def test_CMD01_empty_and_whitespace_are_messages() -> None:
    assert parse_command("").kind == "message"
    assert parse_command("   ").kind == "message"
    assert parse_command("/").kind == "message"


def test_CMD01_overlong_name_rejected() -> None:
    # §25.7：command name ≤ 32，超长不得进入分发。
    parsed = parse_command("/" + "a" * 33)
    assert parsed.kind == "message"
