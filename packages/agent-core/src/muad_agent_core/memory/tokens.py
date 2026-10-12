"""Deterministic CJK bigrams and Latin word matching; no synonym inference."""

import re
import unicodedata

_TERMS = re.compile(r"[a-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]+")


def tokenize(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for match in _TERMS.finditer(normalized):
        term = match.group()
        if term[0].isascii():
            tokens.add(term)
        else:
            tokens.update(term[index:index + 2] for index in range(len(term) - 1))
    return frozenset(tokens)


def lexical_coverage(query: str, content: str) -> float:
    wanted = tokenize(query)
    return len(wanted & tokenize(content)) / len(wanted) if wanted else 0.0
