"""Console deletion controls consistently expose the danger intent."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "apps/console-platform/frontend/src"


def test_all_delete_confirmations_use_danger() -> None:
    controls = []
    for path in (SRC / "modules").rglob("*.tsx"):
        source = path.read_text()
        for match in re.finditer(
            r"<(?:ConfirmAction|Modal)\b.*?</(?:ConfirmAction|Modal)>", source, re.S
        ):
            control = match.group()
            if not re.search(r"t\('[^']*[Dd]elete[^']*'\)", control):
                continue
            controls.append(path)
            danger = (
                'type="danger"' in control
                or 'okType="danger"' in control
                or ("okButtonProps={{" in control and "danger" in control)
            )
            assert danger, str(path)
    assert len(controls) >= 10


def test_confirm_action_propagates_intent_to_trigger_and_confirmation() -> None:
    source = (SRC / "components/common/ConfirmAction.tsx").read_text()
    assert "type?: 'primary' | 'danger'" in source
    assert "type = 'primary'" in source
    assert "okType={type}" in source
    assert '<Button theme={theme} type={type}' in source
