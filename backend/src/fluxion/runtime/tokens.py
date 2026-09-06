"""内部文本 token 估算工具（FEAT-06，单一实现）。

从 `runtime/memory.py` 提取的既有估算算法，供 execution.step 输入估算与
SessionMemoryStore 落库共用。语义是“当前输入文本估算”，不是完整 Prompt
大小，更不是 Provider 计费 usage——调用方必须用 `token_source=estimate` /
`token_scope=input_message` 口径标记，不得冒充 usage。

算法：拉丁文按词计（split）；CJK 无空格分词，按单字计（≈1 token/字，
略过估但远胜低估；过估早 flush 不丢数据）。空输入保持下限 1。
"""

from __future__ import annotations


def estimate_text_tokens(content: str) -> int:
    """估算单段文本的 token 数（内部估算，非计费口径）。"""
    if not content:
        return 1
    word_tokens = len(content.split())
    cjk_chars = sum(1 for char in content if _is_cjk(ord(char)))
    return max(1, word_tokens + cjk_chars)


_CJK_RANGES = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x2E80, 0x9FFF),    # CJK Radicals / Unified Ideographs
    (0xA000, 0xA4FF),    # Yi
    (0xAC00, 0xD7AF),    # Hangul Syllables
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0xFE30, 0xFE4F),    # CJK Compatibility Forms
    (0xFF00, 0xFFEF),    # Fullwidth / Halfwidth
    (0x3000, 0x30FF),    # CJK Symbols / Hiragana / Katakana
    (0x20000, 0x2FA1F),  # CJK Extensions A–F
)


def _is_cjk(codepoint: int) -> bool:
    return any(low <= codepoint <= high for low, high in _CJK_RANGES)


__all__ = ["estimate_text_tokens"]
