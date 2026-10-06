"""把字节**原子地**发布到一个路径（只有目标不存在才发布）——共享产物存储的写入原语。

**为什么值得单独一个原语**：这个「临时文件 + 原子发布 + 已存在即失败」的形状在本仓出现过三份
实现（`NfsArtifactStore.write`、runtime 的 `write_immutable`、网关的入站附件落盘），其中两份写成
`path.exists()` 再 `os.replace(...)` —— 检查与发布之间有窗口，两个写者**都能通过检查、都能"成功"**，
后者静默覆盖前者。网关那处因此产出过「两次 persist 都返回成功、内容只剩一份、另一份引用的
checksum 与磁盘对不上」（2026-10-06 评审 #7）；而它当初正是照着这条注释所描述的契约写的。

**正确做法是让内核替我们判定**：`os.link` 在目标已存在时失败，这是一次**原子**判定，不需要
"先检查"这一步。临时文件先落盘再建链，所以读方要么看不到文件、要么看到完整内容（不会读到半截）。

**部署前提**：目标文件系统要支持硬链接（本地文件系统与 NFS 都支持；少数对象存储型挂载不支持）。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def publish_if_absent(path: Path, data: bytes) -> None:
    """把 `data` 发布到 `path`；目标已存在则抛 `FileExistsError`（**绝不覆盖**）。

    父目录不存在会自动创建；失败时临时文件一定被清掉（不留半成品）。
    调用方需要"重放语义"（同内容复用 / 不同内容冲突）时，在本原语之上自己判定——
    本函数只负责"要么完整发布、要么什么都没发生"。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        tmp.write_bytes(data)
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


__all__ = ["publish_if_absent"]
