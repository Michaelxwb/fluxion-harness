import io
import zipfile

ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME), content)
    return buffer.getvalue()


def skill_md(
    name: str = "Demo Skill",
    description: str = "A demo skill.",
    execution: str | None = None,
    platform_label: str | None = None,
) -> str:
    lines = ["---", f"name: {name}", f"description: {description}"]
    if execution is not None:
        lines.append(f"execution: {execution}")
    if platform_label is not None:
        lines.append(f"platform_label: {platform_label}")
    lines.extend(["---", "", f"# {name}", "", "Follow the instructions.", ""])
    return "\n".join(lines)


def demo_package(
    name: str = "Demo Skill",
    description: str = "A demo skill.",
    execution: str | None = None,
    platform_label: str | None = None,
    extra_files: dict[str, str | bytes] | None = None,
    top_dir: str | None = None,
) -> bytes:
    prefix = f"{top_dir}/" if top_dir else ""
    files: dict[str, str | bytes] = {
        f"{prefix}SKILL.md": skill_md(name, description, execution, platform_label),
        f"{prefix}scripts/run.py": "print('run')\n",
        f"{prefix}references/notes.md": "# Notes\n",
    }
    if extra_files:
        files.update({f"{prefix}{path}": content for path, content in extra_files.items()})
    return zip_bytes(files)


def symlink_package(target: str = "/etc/passwd") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link.py", date_time=ZIP_DATE_TIME)
        info.external_attr = 0o120777 << 16
        archive.writestr(info, target)
    return buffer.getvalue()


def many_entries_package(count: int) -> bytes:
    files: dict[str, str | bytes] = {f"assets/file-{index}.txt": "x" for index in range(count)}
    files["SKILL.md"] = skill_md()
    return zip_bytes(files)
