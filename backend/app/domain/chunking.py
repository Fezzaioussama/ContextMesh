"""Heading-aware, token-bounded chunking with deterministic chunk identities."""

import re
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from app.domain.answers import Locator
from app.domain.errors import IngestionFailure
from app.domain.knowledge import ChunkDraft, Element

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]")
CHUNK_NAMESPACE = UUID("6f1d3c56-6a0e-4c71-9a43-8d2b7f1e0c55")


@dataclass(frozen=True)
class ChunkingPolicy:
    """Token estimates undercount unspaced scripts, so characters are also bounded."""

    max_tokens: int = 400
    overlap_tokens: int = 60
    max_chars: int = 2000
    max_chunks: int = 2000

    @property
    def signature(self) -> str:
        return f"chunker-v2:{self.max_tokens}:{self.overlap_tokens}:{self.max_chars}"


def estimate_tokens(text: str) -> int:
    """Approximate count of word and punctuation tokens; not a provider tokenizer."""
    return len(TOKEN_PATTERN.findall(text))


def chunk_elements(
    elements: tuple[Element, ...], policy: ChunkingPolicy, seed: str
) -> tuple[ChunkDraft, ...]:
    groups = _grouped(elements, policy)
    if len(groups) > policy.max_chunks:
        raise IngestionFailure("document_too_large")
    return tuple(_draft(group, ordinal, seed) for ordinal, group in enumerate(groups))


def _grouped(elements: tuple[Element, ...], policy: ChunkingPolicy) -> list[list[Element]]:
    groups: list[list[Element]] = []
    for piece in _pieces(elements, policy):
        _place(groups, piece, policy)
    return groups


def _pieces(elements: tuple[Element, ...], policy: ChunkingPolicy) -> list[Element]:
    return [piece for element in elements for piece in _bounded(element, policy)]


def _bounded(element: Element, policy: ChunkingPolicy) -> list[Element]:
    if _fits(estimate_tokens(element.text), len(element.text), policy):
        return [element]
    return [replace(element, text=part) for part in _word_windows(element.text, policy)]


def _fits(tokens: int, characters: int, policy: ChunkingPolicy) -> bool:
    return tokens <= policy.max_tokens and characters <= policy.max_chars


def _word_windows(text: str, policy: ChunkingPolicy) -> list[str]:
    words = [part for word in text.split() for part in _slices(word, policy.max_chars)]
    return [" ".join(window) for window in _windows(words, policy)]


def _slices(word: str, size: int) -> list[str]:
    return [word[start : start + size] for start in range(0, len(word), size)]


def _windows(words: list[str], policy: ChunkingPolicy) -> list[list[str]]:
    windows: list[list[str]] = [[]]
    tokens = characters = 0
    for word in words:
        if windows[-1] and not _fits(
            tokens + estimate_tokens(word), characters + len(word), policy
        ):
            windows.append([])
            tokens = characters = 0
        windows[-1].append(word)
        tokens += estimate_tokens(word)
        characters += len(word) + 1
    return windows


def _place(groups: list[list[Element]], piece: Element, policy: ChunkingPolicy) -> None:
    if _starts_section(groups, piece):
        groups.append([piece])
        return
    current = groups[-1]
    if _fits_group(current, piece, policy):
        current.append(piece)
        return
    groups.append([*_overlap(current, piece, policy), piece])


def _fits_group(group: list[Element], piece: Element, policy: ChunkingPolicy) -> bool:
    tokens = _tokens(group) + estimate_tokens(piece.text)
    characters = sum(len(element.text) + 2 for element in group) + len(piece.text)
    return _fits(tokens, characters, policy)


def _starts_section(groups: list[list[Element]], piece: Element) -> bool:
    return not groups or groups[-1][-1].heading_path != piece.heading_path


def _tokens(group: list[Element]) -> int:
    return sum(estimate_tokens(element.text) for element in group)


def _overlap(current: list[Element], piece: Element, policy: ChunkingPolicy) -> list[Element]:
    budget = min(policy.overlap_tokens, policy.max_tokens - estimate_tokens(piece.text))
    carried: list[Element] = []
    for element in reversed(current):
        budget -= estimate_tokens(element.text)
        if budget < 0:
            break
        carried.insert(0, element)
    return carried if _fits_group(carried, piece, policy) else []


def _draft(group: list[Element], ordinal: int, seed: str) -> ChunkDraft:
    text = "\n\n".join(element.text for element in group)
    locator = Locator(
        group[0].heading_path,
        min(element.line_start for element in group),
        max(element.line_end for element in group),
    )
    chunk_id = uuid5(CHUNK_NAMESPACE, f"{seed}:{ordinal}")
    return ChunkDraft(chunk_id, ordinal, text, locator, estimate_tokens(text))
