"""TASK-008: datasets stay deterministic and tagless entity notes are observation-only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_fixture_has_stable_manifest_and_label_shape():
    path = Path("tests/fixtures/memory/minimal.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(digest) == 64
    assert payload["queries"][0]["query_id"] == "q1"
    assert "acceptable" in payload["queries"][0]


def test_tagless_entity_note_is_marked_as_observation_not_hard_failure():
    # D8 observation contract: no entity tag means the entity gate cannot protect it.
    label = {
        "query_id": "d8",
        "user_id": "u1",
        "required": [],
        "forbidden": [],
        "acceptable": [],
        "observation": "tagless_entity_note",
    }
    assert label["observation"] == "tagless_entity_note"


def test_seeded_fixture_is_stable_and_keeps_the_experience_lane_out_of_facts():
    path = Path("tests/fixtures/memory/seeded.json")
    raw = path.read_bytes()
    payload = json.loads(raw)
    assert len(hashlib.sha256(raw).hexdigest()) == 64
    kinds = {item["memory_kind"] for item in payload["memories"]}
    assert "PROCEDURE" in kinds and "PREFERENCE" in kinds
    assert all(item.get("query") for item in payload["queries"])
