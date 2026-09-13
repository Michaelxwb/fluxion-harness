"""Read executable contracts directly from their owning design documents."""

import json
import re
from pathlib import Path
from typing import cast

DOCS = Path(__file__).resolve().parents[1] / "docs"
CAPABILITY_DOC = "02-模块设计/07-Capability-Runtime/design-full.md"
CHANNEL_DOC = "02-模块设计/10-Channel-Gateway/design-full.md"
AGENT_DOC = "02-模块设计/04-Agent-Core与Agent-Executor/design-full.md"
AGENT_FRONTEND = "03-前端设计/03-智能体管理/design-frontend.md"


def contract_text(document: str, identifier: str, language: str = "json") -> str:
    source = (DOCS / document).read_text()
    marker = re.escape(identifier)
    pattern = (
        rf"<!-- contract:{marker} -->\s*```{language}\s*"
        rf"(.*?)```\s*<!-- /contract:{marker} -->"
    )
    matches: list[str] = re.findall(pattern, source, re.S)
    assert len(matches) == 1, f"Expected one {identifier} contract in {document}"
    return matches[0].strip()


def object_contract(document: str, identifier: str) -> dict[str, object]:
    value: object = json.loads(contract_text(document, identifier))
    assert isinstance(value, dict), f"{identifier} must be an object"
    return cast(dict[str, object], value)


def capability_examples() -> list[dict[str, object]]:
    value: object = json.loads(contract_text(CAPABILITY_DOC, "capability-implementation-examples"))
    assert isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, object]], value)


def scenario_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for directory in ("02-模块设计", "03-前端设计"):
        for path in (DOCS / directory).glob("*/design-*.md"):
            for line in path.read_text().splitlines():
                match = re.match(r"^\| ((?:S|E|B|I)-[A-Z0-9]+-\d+) \|", line)
                if match:
                    assert match[1] not in rows, f"Duplicate scenario {match[1]} in {path}"
                    assert line.count("|") >= 7, f"Malformed scenario {match[1]} in {path}"
                    rows[match[1]] = line
    return rows
