import shutil
import subprocess
from pathlib import Path

import pytest
from muad_api import AppError
from muad_console_platform.infrastructure.skill_validator import (
    TRUSTED_REVIEW_CHECKSUMS_ENV,
    checksum_of,
    validated_package,
)

_SKILLS = ("self-skill-review", "self-skill-creator", "self-skill-upload")


def test_built_packages_use_the_same_managed_review_contract(tmp_path: Path, monkeypatch) -> None:
    skills = Path(__file__).resolve().parents[2] / "skills"
    shutil.copy2(skills / "package.sh", tmp_path)
    for name in _SKILLS:
        shutil.copytree(skills / name, tmp_path / name)
    subprocess.run(["bash", str(tmp_path / "package.sh"), *_SKILLS], check=True)
    release = (tmp_path / "self-skill-review.zip").read_bytes()
    monkeypatch.delenv(TRUSTED_REVIEW_CHECKSUMS_ENV, raising=False)
    with pytest.raises(AppError), validated_package(release):
        pytest.fail("Untrusted archive was admitted")
    monkeypatch.setenv(TRUSTED_REVIEW_CHECKSUMS_ENV, checksum_of(release))
    for name in _SKILLS:
        with validated_package((tmp_path / f"{name}.zip").read_bytes()) as package:
            assert "skill-drafts/<key>" in package.instructions
            assert "REVIEW_FINDINGS" in package.instructions
            if name == "self-skill-review":
                assert package.frontmatter["result_contract"]["checksum"] == checksum_of(release)
                assert package.entrypoint == "scripts/check.py"
            else:
                assert package.manifest.result_contract is None
