"""Console deletion controls consistently expose the danger intent."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"


def test_all_delete_confirmations_use_danger() -> None:
    controls = []
    for path in (SRC / "modules").rglob("*.tsx"):
        for match in re.finditer(r"<ConfirmAction\b.*?</ConfirmAction>", path.read_text(), re.S):
            control = match.group()
            if re.search(r"t\('[^']*[Dd]elete[^']*'\)", control):
                controls.append(path)
                assert 'type="danger"' in control, str(path)
    assert len(controls) >= 10


def test_confirm_action_propagates_intent_to_trigger_and_confirmation() -> None:
    source = (SRC / "components/common/ConfirmAction.tsx").read_text()
    assert "type?: 'primary' | 'danger'" in source
    assert "type = 'primary'" in source
    assert "okType={type}" in source
    assert '<Button theme={theme} type={type}' in source
