import shutil
import subprocess
from pathlib import Path

from muad_console_platform.infrastructure.skill_validator import checksum_of, validated_package

_SKILLS = ("self-skill-review", "self-skill-creator", "self-skill-upload")


def test_built_packages_use_the_same_managed_review_contract(tmp_path: Path, monkeypatch) -> None:
    skills = Path(__file__).resolve().parents[2] / "skills"
    shutil.copy2(skills / "package.sh", tmp_path)
    for name in _SKILLS:
        shutil.copytree(skills / name, tmp_path / name)
    subprocess.run(["bash", str(tmp_path / "package.sh"), *_SKILLS], check=True)
    release = (tmp_path / "self-skill-review.zip").read_bytes()
    monkeypatch.delenv("MUAD_TRUSTED_SKILL_REVIEW_CHECKSUMS", raising=False)
    for name in _SKILLS:
        with validated_package((tmp_path / f"{name}.zip").read_bytes()) as package:
            assert "skill-drafts/<key>" in package.instructions
            assert "result.reviewed_draft" in package.instructions
            if name == "self-skill-review":
                assert package.frontmatter["result_contract"]["checksum"] == checksum_of(release)
                assert package.entrypoint == "scripts/check.py"
            else:
                assert package.manifest.result_contract is None
