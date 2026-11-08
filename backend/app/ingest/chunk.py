"""Split parsed sections into retrieval chunks.

Heading-aware: a chunk never spans two sections, so a passage keeps the heading it belongs
to, and each chunk carries its heading path ("Benefits › Paid time off") as context for
embedding and for citations. Long sections split at paragraph, then sentence, boundaries to
stay under `max_tokens`, with one sentence of overlap between neighbours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import Section

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class ChunkText:
    heading: str
    text: str

    def for_embedding(self, title: str) -> str:
        """Document title and heading in front of the passage: cheap contextual retrieval."""
        context = " › ".join(p for p in (title, self.heading) if p)
        return f"{context}\n\n{self.text}" if context else self.text


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.3))


def _split_long(text: str, max_tokens: int) -> list[str]:
    """Pack paragraphs (then sentences) into pieces of at most max_tokens."""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if estimate_tokens(paragraph) <= max_tokens:
            units.append(paragraph)
        else:
            units.extend(s.strip() for s in _SENTENCE.split(paragraph) if s.strip())

    pieces: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and estimate_tokens(" ".join(current + [unit])) > max_tokens:
            pieces.append("\n\n".join(current))
            last = _SENTENCE.split(current[-1])[-1]  # one sentence of overlap
            current = [last] if estimate_tokens(last) < max_tokens // 4 else []
        current.append(unit)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_sections(sections: list[Section], max_tokens: int = 400) -> list[ChunkText]:
    chunks: list[ChunkText] = []
    for section in sections:
        for piece in _split_long(section.text, max_tokens):
            if len(piece.strip()) >= 3:
                chunks.append(ChunkText(section.heading, piece))
    return chunks
