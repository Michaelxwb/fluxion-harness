"""附件门控：纯函数、无 IO 的准入判定。

**为什么需要**：业务规则要求「收不了的消息必须给出明确反馈」（设计 RULE-01），而"能不能收"
的判定原本会散落进渠道分支里。收敛成一个纯函数后，边界值（恰好等于上限 vs 超 1 字节）可以被
单测钉死，且**不随任何渠道的取件方式变化**（设计 AD-8）。

**P0 以模块常量生效**，P1 再改成读配置（设计 §2.3.2 / API-07）。常量同时是用户可见提示里
数值的来源——提示里的上限值必须与这里一致，不能各写一份。

**产物键只由系统生成**（`build_storage_key`）：用户提供的文件名**只作为元信息**保留，
绝不参与路径拼接——否则 `../../etc/passwd` 这类名字会直接变成路径穿越。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# 超限只有一种说法：码定义在中立的渠道边界词汇表里，这里只引用（单一来源）
from ..channels.base import ATTACHMENT_TOO_LARGE

MIB = 1024 * 1024

#: 单文件大小上限。覆盖常见截图与办公文档；避免单条消息拖垮下载与上下文。
MAX_ATTACHMENT_BYTES = 20 * MIB
#: 单消息附件数上限。与「只内联当前消息图片」的成本口径匹配（设计 AD-3-B）。
MAX_ATTACHMENTS_PER_MESSAGE = 5

#: 类型白名单（按 MIME，与 `AttachmentRef.media_type` 同口径）。
#: 图片对齐模型的视觉输入能力；文档对齐 FEAT-06 的抽取能力（pdf/docx/xlsx/pptx/txt/md）。
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        # docx / xlsx / pptx 的官方 MIME 较长，是不可省的：按扩展名猜会漏判
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
    }
)

ATTACHMENT_COUNT_EXCEEDED = "ATTACHMENT_COUNT_EXCEEDED"
ATTACHMENT_TYPE_NOT_ALLOWED = "ATTACHMENT_TYPE_NOT_ALLOWED"


@dataclass(frozen=True, slots=True)
class AttachmentCandidate:
    """门控输入：**下载之前**就能拿到的信息（渠道侧给出的媒体元信息）。

    刻意不含取件凭据与存储键——门控在下载前判定，此时既没有字节也没有产物键（设计 AD-8/AD-1-B）。
    """

    media_type: str
    size: int
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class GateRejection:
    index: int
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class GateDecision:
    accepted: tuple[AttachmentCandidate, ...]
    rejected: tuple[GateRejection, ...]

    @property
    def ok(self) -> bool:
        return not self.rejected


def evaluate_gate(candidates: Sequence[AttachmentCandidate]) -> GateDecision:
    """逐个判定，**部分失败不拖累其余**（设计 §4.2 边缘情况）。

    超出数量上限的按位置丢弃（第 N+1 个起），已通过的仍会落盘——用户不会因为多发了一个
    就整条消息白费。
    """
    accepted: list[AttachmentCandidate] = []
    rejected: list[GateRejection] = []
    for index, candidate in enumerate(candidates):
        if index >= MAX_ATTACHMENTS_PER_MESSAGE:
            rejected.append(
                GateRejection(
                    index,
                    ATTACHMENT_COUNT_EXCEEDED,
                    f"单条消息最多 {MAX_ATTACHMENTS_PER_MESSAGE} 个附件",
                )
            )
            continue
        if candidate.media_type not in ALLOWED_MEDIA_TYPES:
            rejected.append(
                GateRejection(index, ATTACHMENT_TYPE_NOT_ALLOWED, f"不支持的媒体类型：{candidate.media_type}")
            )
            continue
        if candidate.size > MAX_ATTACHMENT_BYTES:
            rejected.append(
                GateRejection(
                    index,
                    ATTACHMENT_TOO_LARGE,
                    f"超过单文件上限 {MAX_ATTACHMENT_BYTES} 字节",
                )
            )
            continue
        accepted.append(candidate)
    return GateDecision(tuple(accepted), tuple(rejected))


def build_storage_key(*, token: str, index: int) -> str:
    """产物键只由系统生成：`token` 由调用方给（消息/会话级不透明 id），**不含用户输入**。"""
    return f"inbound/{token}/{index}"


@dataclass(frozen=True, slots=True)
class PrecheckDecision:
    """预检结论：`accepted` 是**个数**——按回调顺序的前 N 个通过。"""

    accepted: int
    rejected: tuple[GateRejection, ...]

    @property
    def ok(self) -> bool:
        return not self.rejected


def evaluate_precheck(count: int) -> PrecheckDecision:
    """① 预检：**只判数量**（设计 §3.2.1 / API-07）。

    输入就是"本消息识别出几个媒体项"——这是取件之前**唯一的已知量**：企微回调不带 `size`、
    MIME 与文件名（设计 §3.2.2），类型与大小都只能在取件解密之后判（见 `evaluate_gate`）。

    **为什么输入是计数而不是某种候选类型**：预检除了"第几个"之外没有任何可判的东西；为它造一个
    候选类型必然会带进渠道私有的取件引用，而核心域不得看见任何渠道私有形状（RULE-07 / AD-8）。
    """
    rejected = tuple(
        GateRejection(
            index,
            ATTACHMENT_COUNT_EXCEEDED,
            f"单条消息最多 {MAX_ATTACHMENTS_PER_MESSAGE} 个附件",
        )
        for index in range(MAX_ATTACHMENTS_PER_MESSAGE, max(count, 0))
    )
    return PrecheckDecision(min(max(count, 0), MAX_ATTACHMENTS_PER_MESSAGE), rejected)
