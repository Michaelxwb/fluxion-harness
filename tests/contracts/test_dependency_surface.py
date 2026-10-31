"""S-02: the LangGraph stack must leave the dependency surface completely."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK_PREFIXES = ("langgraph", "langchain", "langsmith")


def test_requirements_txt_excludes_the_langgraph_stack() -> None:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    offenders = [
        line
        for line in lines
        if line.split("==", 1)[0].strip().lower().startswith(STACK_PREFIXES)
    ]
    assert offenders == [], f"requirements.txt still pins: {offenders}"


def test_uv_lock_excludes_the_langgraph_stack() -> None:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8").casefold()
    assert "langgraph" not in text
    assert "langchain" not in text


def test_langgraph_is_not_importable() -> None:
    assert importlib.util.find_spec("langgraph") is None
