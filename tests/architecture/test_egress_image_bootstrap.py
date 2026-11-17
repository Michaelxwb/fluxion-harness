"""Every Runtime/Worker image must install the trusted Skill startup hook."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK_INSTALL = "python -m muad_egress_site --install"


@pytest.mark.parametrize("service", ["agent-runtime", "agent-worker"])
@pytest.mark.parametrize("intranet", [False, True], ids=["standard", "intranet"])
def test_service_image_installs_egress_hook_after_packages_as_root(
    service: str, intranet: bool,
) -> None:
    path = (
        ROOT / "build/docker-build" / f"Dockerfile.{service}"
        if intranet else ROOT / "apps" / service / "Dockerfile"
    )
    source = path.read_text(encoding="utf-8")
    steps = re.sub(r"\\\n\s*", " ", source).splitlines()
    installers = [index for index, step in enumerate(steps) if HOOK_INSTALL in step]
    assert len(installers) == 1, f"{path}: install the egress hook exactly once"
    hook_index = installers[0]
    hook_step = steps[hook_index]
    assert hook_step.startswith("RUN "), f"{path}: install during image build"
    package_command = "python -m pip install" if intranet else "uv sync"
    package_index = next(
        index for index, step in enumerate(steps)
        if step.startswith("RUN ") and package_command in step
        and ("/app/packages/*" in step if intranet else f"--package muad-{service}" in step)
    )
    assert package_index <= hook_index, f"{path}: install packages before the hook"
    if package_index == hook_index:
        assert hook_step.index(package_command) < hook_step.index(HOOK_INSTALL)
        assert "&& " + HOOK_INSTALL in hook_step, f"{path}: stop if package installation fails"
    users_before = [step for step in steps[:hook_index] if step.startswith("USER ")]
    assert not users_before or users_before[-1] == "USER root", f"{path}: install as root"
    runtime_user = "USER 10001:10001" if intranet else "USER muad"
    assert runtime_user in steps[hook_index + 1:], f"{path}: retain non-root execution"
