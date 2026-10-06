"""[TASK-004] 附件落盘地基：预检门控（纯函数）+ 落盘服务（共享 artifact store）。

这两块是 TASK-004 的管道底座，先各自钉死再接线：

- **预检**只判数量。输入是"本消息识别出几个媒体项"——取件之前唯一的已知量（企微回调不带
  `size`/MIME/文件名，设计 §3.2.2）。边界与 `B-03` 同口径：恰好 5 个接收、第 6 个起按位置丢弃。
- **落盘**只写字节不写库（AD-1-B）：**目标不存在才发布**（临时文件 + `os.link`，内核级原子）、
  同键**同内容**重放复用已有产物、同键**不同内容**抛 `AttachmentConflictError`（谁也不覆盖谁），
  且**用户文件名不参与路径拼接**（B-04 同族）。
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from muad_artifact_store import NfsArtifactStore
from muad_contracts import AttachmentRef
from muad_im_gateway.application.attachment_gate import (
    ATTACHMENT_COUNT_EXCEEDED,
    MAX_ATTACHMENTS_PER_MESSAGE,
    evaluate_precheck,
)
from muad_im_gateway.application.inbound_attachments import (
    AttachmentConflictError,
    InboundAttachmentStore,
    kind_for,
)
from muad_im_gateway.channels.base import FetchedAttachment

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 3


def _content(data: bytes = PNG, media_type: str = "image/png", filename: str | None = "shot.png"):
    return FetchedAttachment(
        data=data,
        media_type=media_type,
        filename=filename,
        checksum=hashlib.sha256(data).hexdigest(),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("count", "accepted", "rejected"),
    [
        (0, 0, 0),
        (1, 1, 0),
        (MAX_ATTACHMENTS_PER_MESSAGE - 1, MAX_ATTACHMENTS_PER_MESSAGE - 1, 0),
        (MAX_ATTACHMENTS_PER_MESSAGE, MAX_ATTACHMENTS_PER_MESSAGE, 0),
        (MAX_ATTACHMENTS_PER_MESSAGE + 1, MAX_ATTACHMENTS_PER_MESSAGE, 1),
        (MAX_ATTACHMENTS_PER_MESSAGE + 3, MAX_ATTACHMENTS_PER_MESSAGE, 3),
    ],
)
def test_precheck_rejects_only_beyond_the_count_limit(count: int, accepted: int, rejected: int) -> None:
    """B-03 同口径：恰好等于上限接收，超出部分按位置丢弃（不是整条拒绝）。"""
    decision = evaluate_precheck(count)

    assert decision.accepted == accepted
    assert len(decision.rejected) == rejected
    assert decision.ok is (rejected == 0)
    for index, rejection in enumerate(decision.rejected, start=MAX_ATTACHMENTS_PER_MESSAGE):
        assert rejection.index == index
        assert rejection.code == ATTACHMENT_COUNT_EXCEEDED


@pytest.mark.unit
def test_precheck_treats_negative_count_as_empty() -> None:
    """防御：计数不可能为负，但真拿到负值也不该崩——按 0 处理。"""
    assert evaluate_precheck(-1).accepted == 0
    assert evaluate_precheck(-1).rejected == ()


@pytest.mark.integration
def test_persist_writes_bytes_and_returns_a_relative_reference(tmp_path) -> None:
    """S-07 同族：字节真的落到共享 store，产出的引用是**相对键** + 元信息（AD-1-B）。"""
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))

    ref = store.persist(token="msg-1", index=0, content=_content(), source_channel="WECOM")

    assert ref.storage_key == "inbound/msg-1/0"
    assert not ref.storage_key.startswith("/"), "DB 只存相对 key（RULE-skill-001）"
    assert ref.kind == "IMAGE" and ref.media_type == "image/png"
    assert ref.size == len(PNG) and ref.filename == "shot.png"
    assert ref.checksum == hashlib.sha256(PNG).hexdigest()
    assert (tmp_path / ref.storage_key).read_bytes() == PNG


@pytest.mark.integration
def test_persist_is_atomic_and_leaves_no_temporary_files(tmp_path) -> None:
    """原子写：落地后目录里只有产物本身，没有 `.tmp-*` 残留（不留半成品）。"""
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))

    store.persist(token="msg-2", index=0, content=_content(), source_channel="WECOM")

    leftovers = [path.name for path in (tmp_path / "inbound" / "msg-2").iterdir()]
    assert leftovers == ["0"], f"残留了临时文件：{leftovers}"


@pytest.mark.integration
def test_persist_reuses_the_existing_bytes_for_a_same_content_replay(tmp_path) -> None:
    """同键**同内容**重放：复用已有产物，不报错 —— 消息重投必须能走到 Runtime 的幂等提交。

    此前是"存在即 `FileExistsError`"：Redis 降级（fail-open 不去重）或去重键过期后，同一条媒体
    消息**永远**走不到 Runtime（实测：重投直接抛 `FileExistsError`，Runtime 只收到第一次那次
    失败请求）。
    """
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))
    first = store.persist(token="msg-3", index=0, content=_content(), source_channel="WECOM")

    replayed = store.persist(token="msg-3", index=0, content=_content(), source_channel="WECOM")

    assert replayed.storage_key == first.storage_key
    assert replayed.checksum == first.checksum
    assert replayed.size == len(PNG)
    assert (tmp_path / "inbound/msg-3/0").read_bytes() == PNG


@pytest.mark.integration
def test_persist_refuses_to_reuse_the_key_for_different_content(tmp_path) -> None:
    """同键**不同内容**：明确冲突，既不覆盖别人的字节，也不把那些字节当成自己的。

    文件在、但内容不同是两回事：前者是重投，后者说明有人在同一个键上换了东西。
    """
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))
    store.persist(token="msg-3", index=0, content=_content(), source_channel="WECOM")

    with pytest.raises(AttachmentConflictError):
        store.persist(
            token="msg-3", index=0, content=_content(b"different"), source_channel="WECOM"
        )

    assert (tmp_path / "inbound/msg-3/0").read_bytes() == PNG


@pytest.mark.integration
def test_concurrent_writers_publish_exactly_once(tmp_path) -> None:
    """并发写同一个键：**只有一个赢**，另一个要么复用、要么冲突——绝不静默覆盖。

    旧实现是 `path.exists()` 再 `os.replace`：两个写者都能通过存在性检查、都能"成功"，
    后写的覆盖先写的（实测：两份引用里有一份的 checksum 与磁盘内容对不上）。
    """
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))
    barrier = threading.Barrier(2)
    original = store._publish_new

    def synchronized(path, data):  # type: ignore[no-untyped-def]
        barrier.wait(timeout=5)
        original(path, data)

    store._publish_new = synchronized  # type: ignore[method-assign]
    contents = [_content(b"first"), _content(b"second")]
    results: list[object] = []

    def write(content: FetchedAttachment) -> None:
        try:
            results.append(
                store.persist(token="race", index=0, content=content, source_channel="WECOM")
            )
        except AttachmentConflictError as exc:
            results.append(exc)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(write, contents))

    written = (tmp_path / "inbound/race/0").read_bytes()
    assert written in (b"first", b"second")
    winners = [r for r in results if not isinstance(r, AttachmentConflictError)]
    losers = [r for r in results if isinstance(r, AttachmentConflictError)]
    assert len(winners) == 1 and len(losers) == 1, results
    winner = winners[0]
    assert isinstance(winner, AttachmentRef)
    # 赢家给出的引用必须与磁盘内容一致（否则调用方会拿着对不上的 checksum 往下走）
    assert winner.checksum == hashlib.sha256(written).hexdigest()


@pytest.mark.integration
def test_user_filename_never_participates_in_the_path(tmp_path) -> None:
    """B-04 同族：`../../etc/passwd` 这类文件名只作元信息，产物键由系统生成。"""
    store = InboundAttachmentStore(NfsArtifactStore(tmp_path))

    ref = store.persist(
        token="msg-4",
        index=0,
        content=_content(filename="../../etc/passwd"),
        source_channel="WECOM",
    )

    written = tmp_path / ref.storage_key
    assert written.resolve().is_relative_to(tmp_path.resolve()), "产物必须落在 store 根内"
    assert ref.filename == "../../etc/passwd", "原文件名作为元信息保留，不丢失也不参与拼接"


@pytest.mark.unit
def test_kind_is_derived_from_media_type() -> None:
    """`kind` 由 MIME 派生；只有通过门控的媒体才会走到这里，故非图片即文档。"""
    assert kind_for("image/png") == "IMAGE"
    assert kind_for("application/pdf") == "DOCUMENT"
    assert kind_for("text/markdown") == "DOCUMENT"
