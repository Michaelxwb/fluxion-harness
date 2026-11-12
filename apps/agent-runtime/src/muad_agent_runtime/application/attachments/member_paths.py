"""ZIP 成员路径的规范化：**打包与读取共用的唯一实现**。

两条路径判的是同一件事（这个名字能不能安全地进包/出包），各写一份就会漂移：
打包拒绝的名字被读取放行，或者反过来。规则本身只在这里增长。

- **NFC 规范化**：NFC 与 NFD 是同一个名字（`é` 的两种编码）。规范化后二者自然撞成
  "重复路径"，由调用方按既有规则拒绝。
- **控制/格式字符按 Unicode 类别判**：`Cc`（含 C1 控制符）与 `Cf`（零宽字符、双向覆盖符，
  如 U+202E）会造成显示欺骗（文件名看起来是 `gpj.png`，实际是 `gnp.png`）。
- **纯空白段**：`" "`、`"a/ /b"` 这类既不等于 `{"", ".", ".."}`，也不落字符类别检查。
"""

from __future__ import annotations

import unicodedata

MAX_ARCHIVE_PATH_BYTES = 1024


class MemberPathError(ValueError):
    """成员路径不合法。消息可直接展示给用户/模型，不含内部细节。"""


def normalize_member_path(value: object) -> str:
    """规范化并校验成员路径；不合法时抛 `MemberPathError`（消息即用户可见原因）。

    合法路径可能被**改写**（NFC 规范化），调用方必须使用返回值而不是原始输入。
    """
    if not isinstance(value, str) or not value:
        raise MemberPathError("文件路径必须是非空相对路径")
    path = unicodedata.normalize("NFC", value)
    if (
        "\\" in path
        or ":" in path
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in path)
        or any(part in {"", ".", ".."} or not part.strip() for part in path.split("/"))
        or _too_long(path)
    ):
        raise MemberPathError("文件路径不能包含绝对路径、目录逃逸或无效字符")
    return path


def _too_long(path: str) -> bool:
    """按 UTF-8 字节判长度：字符数相同的 CJK 名字比 ASCII 长 3 倍。"""
    try:
        return len(path.encode("utf-8")) > MAX_ARCHIVE_PATH_BYTES
    except UnicodeEncodeError:
        return True
