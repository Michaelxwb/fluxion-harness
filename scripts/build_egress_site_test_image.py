"""Build the S-04 test image from locked dependencies and selected local wheels."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

MODULES = ("common", "contracts", "logging-kit", "api-kit", "platform-sdk")


def build(image: str, base_image: str) -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/sdk/fixtures/egress_site"
    with tempfile.TemporaryDirectory(prefix="egress-site-build-") as directory:
        context = Path(directory)
        for module in MODULES:
            shutil.copytree(root / "packages" / module, context / "packages" / module,
                            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", ".venv"))
        for name in ("Dockerfile", "probe.py"):
            shutil.copy(fixture / name, context / name)
        requirements = subprocess.run([
            "uv", "export", "--frozen", "--package", "muad-platform-sdk", "--no-dev",
            "--no-emit-workspace", "--no-hashes", "--no-annotate", "--no-header",
            "--format", "requirements-txt",
        ], cwd=root, check=True, capture_output=True, text=True)
        (context / "requirements.txt").write_text(requirements.stdout, encoding="utf-8")
        subprocess.run(["docker", "build", "--progress=plain", "--build-arg", f"BASE_IMAGE={base_image}",
                        "-t", image, str(context)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="fluxion-egress-site-test:local")
    parser.add_argument("--base-image", default="fluxion-egress-sandbox-test:local")
    arguments = parser.parse_args()
    build(arguments.image, arguments.base_image)
