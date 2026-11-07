"""新平台模板与 smoke_platform 样例：包结构可加载，端到端链路可跑通。"""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

from muad_console_platform.infrastructure.skill_validator import validated_package
from muad_skill_sdk.skill_package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "skills/_templates/business-skill-python"
SAMPLE = ROOT / "skills/smoke-platform-sample"
SMOKE = ROOT / "tools/fake-business-platform/smoke_check.py"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def test_template_and_sample_packages_load_and_validate() -> None:
    template = SkillPackage.load(TEMPLATE)
    assert template.manifest.name == "business-skill-python-template"
    assert template.manifest.platforms == ("example_platform",)

    sample = SkillPackage.load(SAMPLE)
    assert sample.manifest.name == "smoke-platform-sample"
    assert sample.manifest.platforms == ("smoke_platform",)

    for directory in (TEMPLATE, SAMPLE):
        with validated_package(_zip_directory(directory)) as package:
            assert package.manifest.name == SkillPackage.load(directory).manifest.name


def test_smoke_platform_end_to_end() -> None:
    completed = subprocess.run(
        [sys.executable, str(SMOKE)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "smoke ok" in completed.stdout
