"""草稿测试数据的共享目录与互斥键；不依赖进程内状态。"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path


def draft_test_lock_key(tenant_id: str, owner_user_id: str, draft_id: str) -> int:
    """Runtime 测试与 Console 删除使用同一 PostgreSQL advisory lock 身份。"""
    if not tenant_id or "\x00" in tenant_id:
        raise ValueError("invalid draft test tenant")
    components = (tenant_id, str(uuid.UUID(owner_user_id)), str(uuid.UUID(draft_id)))
    value = "draft-test\x00" + "\x00".join(components)
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)


def draft_test_data_dir(workspace_dir: Path, draft_id: str) -> Path:
    """测试目录与正式 data、草稿包并列；拒绝把符号链接作为持久挂载源。"""
    root = workspace_dir / "draft-tests" / str(uuid.UUID(draft_id))
    for directory in (workspace_dir, root.parent, root, root / "data"):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("draft test directory must be a regular directory")
    return root / "data"


def prepare_draft_test_data(workspace_dir: Path, draft_id: str, *, reset: bool) -> Path:
    """只在持有测试锁与 workspace 租约时调用；重置不触碰正式数据。"""
    directory = draft_test_data_dir(workspace_dir, draft_id)
    if reset and directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()
