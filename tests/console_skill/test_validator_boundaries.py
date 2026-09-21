"""[B-01~B-04][E-04] 导入校验器真实字节边界与 Secret 不泄露（unit/integration）。"""

from __future__ import annotations

import io
import random
import zipfile

import pytest
from muad_api import AppError
from muad_console_platform.infrastructure import skill_validator

from console_skill.packages import demo_package, skill_md


def _zip_deflated(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _zip_at_total_size(target: int) -> bytes:
    """构造总字节数恰为 target 的合法 zip（STORED 随机填充，大小可精确预测）。"""
    rng = random.Random(20260919)

    def build(pad_size: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", skill_md())
            archive.writestr("scripts/run.py", "print('run')\n")
            if pad_size:
                info = zipfile.ZipInfo("assets/pad.txt")
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, rng.randbytes(pad_size))
        return buffer.getvalue()

    constant = len(build(1)) - 1  # 头/目录开销与 pad 大小线性无关
    return build(target - constant)


def test_b01_zip_size_exact_boundary_pass_and_reject() -> None:
    """[B-01] zip 恰为 50MiB 通过；50MiB+1 拒绝（真实字节边界）。"""
    with skill_validator.validated_package(_zip_at_total_size(skill_validator.ZIP_BYTES_LIMIT)):
        pass  # 边界内通过
    with pytest.raises(AppError) as reject:
        oversized = _zip_at_total_size(skill_validator.ZIP_BYTES_LIMIT) + b"\x00"
        with skill_validator.validated_package(oversized):
            pass
    assert reject.value.code == "SKILL_PACKAGE_INVALID"


def test_b02_unpacked_size_exact_boundary_pass_and_reject() -> None:
    """[B-02] 解压后恰为 200MiB 通过；200MiB+1 拒绝（deflate 零填充控制 zip 体积）。"""
    limit = skill_validator.UNPACKED_BYTES_LIMIT
    base = {
        "SKILL.md": skill_md().encode(),
        "scripts/run.py": b"print('run')\n",
    }
    base_size = len(base["SKILL.md"]) + len(base["scripts/run.py"])
    pad = b"\x00" * (limit - base_size)
    within = _zip_deflated({**base, "assets/blob.csv": pad})
    assert len(within) <= skill_validator.ZIP_BYTES_LIMIT
    with skill_validator.validated_package(within):
        pass  # 边界内通过

    pad_over = b"\x00" * (limit - base_size + 1)
    over = _zip_deflated({**base, "assets/blob.csv": pad_over})
    with pytest.raises(AppError) as reject:
        with skill_validator.validated_package(over):
            pass
    assert reject.value.code == "SKILL_PACKAGE_INVALID"


def test_b03_entry_count_exact_boundary_pass_and_reject() -> None:
    """[B-03] 文件数恰为 2000 通过；2001 拒绝。"""
    limit = skill_validator.ENTRY_LIMIT
    for count, expect_ok in ((limit, True), (limit + 1, False)):
        files = {f"assets/file-{index}.txt": "x" for index in range(count - 2)}
        files["SKILL.md"] = skill_md()
        files["scripts/run.py"] = "print('run')\n"
        package = _zip_deflated({name: content.encode() for name, content in files.items()})
        if expect_ok:
            with skill_validator.validated_package(package):
                pass  # 边界内通过
        else:
            with pytest.raises(AppError) as reject:
                with skill_validator.validated_package(package):
                    pass
            assert reject.value.code == "SKILL_PACKAGE_INVALID"


def test_b04_renamed_nested_archive_is_rejected_by_magic_bytes() -> None:
    """[B-04] 嵌套压缩包改名 .txt 仍被拒（按文件魔数判定，不只扩展名）。"""
    inner = demo_package(name="Inner")
    package = demo_package(extra_files={"assets/payload.txt": inner})
    with pytest.raises(AppError) as reject:
        with skill_validator.validated_package(package):
            pass
    assert reject.value.code == "SKILL_PACKAGE_INVALID"


def test_e04_secret_scan_rejects_and_never_leaks_secret() -> None:
    """[E-04] 命中敏感信息扫描拒绝导入，异常对象不携带 Secret 明文。"""
    secret = "AKIA" + "IOSFODNN7EXAMPLE"[:16]
    leak = f'AWS_KEY = "{secret}"\n'
    package = demo_package(extra_files={"scripts/leak.py": leak})
    with pytest.raises(AppError) as reject:
        with skill_validator.validated_package(package):
            pass
    error = reject.value
    assert error.code == "SKILL_PACKAGE_INVALID"
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in str(error.message_args)
    assert secret not in str(error.data)
