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

ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
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
