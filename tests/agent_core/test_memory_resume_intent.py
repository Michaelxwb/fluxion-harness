"""TASK-014 auxiliary E-05: complete resume and selection forms only."""

from importlib import import_module
from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "text,theme",
    [
        ("继续上次任务", None),
        ("请接着做昨天的任务。", None),
        ("继续季度报告任务", "季度报告"),
        ("请继续上次的季度报告", "季度报告"),
        ("continue", None),
        ("please resume the previous task", None),
        ("resume quarterly report", "quarterly report"),
    ],
)
def test_local_e05_complete_resume_request(text, theme):
    intent = import_module("muad_agent_core.memory.resume").parse_resume_intent(text)
    assert intent is not None and intent.theme == theme


@pytest.mark.parametrize(
    "text",
    [
        "不要继续上次任务",
        "我没有说继续",
        "你觉得继续任务好吗？",
        "继续是什么意思",
        "do not continue",
        "what does resume mean?",
        "继续报告还是不继续？",
        "昨天任务完成了",
        "继续不意味着批准",
        "继续不是让我批准任务",
        "resume is just a word",
        "continue if allowed",
    ],
)
def test_local_e05_negative_question_and_word_occurrence_are_not_resume(text):
    assert import_module("muad_agent_core.memory.resume").parse_resume_intent(text) is None


def options():
    dto = import_module("muad_contracts.memory").MemoryResumeOption
    pairs = [(uuid4(), title) for title in ("季度报告", "发布计划")]
    return tuple(
        dto(
            number=n,
            memory_key=f"task.{root.hex}",
            version=1,
            root_conversation_id=root,
            title=title,
            candidate_hash="sha256:" + "a" * 64,
        )
        for n, (root, title) in enumerate(pairs, 1)
    )


@pytest.mark.parametrize("text", ["第2个", "第二个", "选第2个", "2", "option 2"])
def test_local_e05_complete_choice_works_without_continue_word(text):
    rows = options()
    assert import_module("muad_agent_core.memory.resume").choose_resume_option(text, rows) == rows[1]


@pytest.mark.parametrize("text", ["不要选第2个", "第2个不选", "选第2个吗？", "我说第2个只是举例", "第3个"])
def test_local_e05_choice_does_not_extract_numbers_from_other_sentences(text):
    assert import_module("muad_agent_core.memory.resume").choose_resume_option(text, options()) is None


def test_local_e05_exact_key_and_unique_title_only():
    rows = options()
    choose = import_module("muad_agent_core.memory.resume").choose_resume_option
    assert choose(rows[1].memory_key, rows) == rows[1]
    assert choose(rows[1].title, rows) == rows[1]
    duplicate = (rows[0], rows[1].model_copy(update={"title": rows[0].title}))
    assert choose(rows[0].title, duplicate) is None
