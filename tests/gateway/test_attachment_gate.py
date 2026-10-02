"""[TASK-003] 附件门控：纯函数、无 IO 的准入判定。

门控是「收不了的消息必须给出明确反馈」这条业务规则（设计 RULE-01）的判定source：
它把"能不能收"从散落的分支收敛成一个可单测的纯函数，边界值（恰好等于上限 vs 超 1 字节）
因此可以被钉死。产物键**只由系统生成**，绝不从用户提供的文件名派生。
"""

from __future__ import annotations

from muad_im_gateway.application.attachment_gate import (
    ALLOWED_MEDIA_TYPES,
    ATTACHMENT_COUNT_EXCEEDED,
    ATTACHMENT_TOO_LARGE,
    ATTACHMENT_TYPE_NOT_ALLOWED,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    AttachmentCandidate,
    build_storage_key,
    evaluate_gate,
)

MIB = 1024 * 1024


def _candidate(size: int = 1024, media_type: str = "image/png", filename: str | None = "a.png"):
    return AttachmentCandidate(size=size, media_type=media_type, filename=filename)


def test_constants_match_the_designed_defaults() -> None:
    """P0 默认值必须与设计一致 —— 它们同时是用户可见提示里的数值来源。

    大小上限 **2026-10-03 由 20 MiB 放宽到 50 MiB**（attachment-round-trip TASK-002）：
    企微官方入站回调上限是 100 MB，出站上传天花板 ≈ 50 MB，50 MiB 让收发对齐。
    数量上限沿用 wecom-inbound-media 的 5（本次未动）。
    """
    assert MAX_ATTACHMENT_BYTES == 50 * MIB
    assert MAX_ATTACHMENTS_PER_MESSAGE == 5
    assert "image/png" in ALLOWED_MEDIA_TYPES
    assert "application/pdf" in ALLOWED_MEDIA_TYPES


def test_b01_size_exactly_at_limit_is_accepted() -> None:
    """B-01：单文件恰好等于上限 → 接收（真实边界：门控纯函数）。"""
    decision = evaluate_gate([_candidate(size=MAX_ATTACHMENT_BYTES)])

    assert decision.ok, f"恰好等于上限必须接收，实际被拒：{decision.rejected}"
    assert len(decision.accepted) == 1
    assert not decision.rejected


def test_b02_size_one_byte_over_limit_is_rejected() -> None:
    """B-02：单文件超过上限 1 字节 → 拒绝，原因码为「超大小上限」。"""
    decision = evaluate_gate([_candidate(size=MAX_ATTACHMENT_BYTES + 1)])

    assert not decision.ok
    assert not decision.accepted
    assert len(decision.rejected) == 1
    assert decision.rejected[0].code == ATTACHMENT_TOO_LARGE


def test_b03_attachment_count_boundary() -> None:
    """B-03：附件数 == 上限 → 全收；== 上限 + 1 → 超出的那个被拒，其余仍收。"""
    at_limit = [_candidate() for _ in range(MAX_ATTACHMENTS_PER_MESSAGE)]
    decision = evaluate_gate(at_limit)
    assert decision.ok
    assert len(decision.accepted) == MAX_ATTACHMENTS_PER_MESSAGE

    over = [_candidate() for _ in range(MAX_ATTACHMENTS_PER_MESSAGE + 1)]
    over_decision = evaluate_gate(over)
    assert not over_decision.ok
    # 部分失败不拖累其余：前 N 个仍接收，超出的逐个反馈（设计 §4.2 边缘情况）
    assert len(over_decision.accepted) == MAX_ATTACHMENTS_PER_MESSAGE
    assert len(over_decision.rejected) == 1
    assert over_decision.rejected[0].code == ATTACHMENT_COUNT_EXCEEDED


def test_b04_storage_key_never_contains_user_input() -> None:
    """B-04：产物键只由系统生成 —— 用户提供的文件名/路径片段不得进入键。"""
    hostile = _candidate(filename="../../etc/passwd")
    key = build_storage_key(token="a1b2c3d4", index=0)

    assert "../../" not in key
    assert "etc" not in key
    assert "passwd" not in key
    assert hostile.filename == "../../etc/passwd", "原文件名仍作为元信息保留，只是不参与拼路径"

    empty = _candidate(filename="")
    assert build_storage_key(token="a1b2c3d4", index=1) != ""
    assert empty.filename == ""


def test_type_whitelist_rejects_unknown_media_type() -> None:
    """白名单外的类型一律拒绝（E-01 的判定来源）。"""
    decision = evaluate_gate([_candidate(media_type="application/x-msdownload")])

    assert not decision.ok
    assert decision.rejected[0].code == ATTACHMENT_TYPE_NOT_ALLOWED


def test_decision_is_pure_and_order_preserving() -> None:
    """纯函数：同样输入给同样输出，且接收项保持原顺序（便于按索引回填元信息）。"""
    candidates = [_candidate(media_type="image/png"), _candidate(media_type="application/pdf")]
    first = evaluate_gate(candidates)
    second = evaluate_gate(candidates)

    assert first == second
    assert [item.media_type for item in first.accepted] == ["image/png", "application/pdf"]
