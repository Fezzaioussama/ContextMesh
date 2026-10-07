"""Shared parser helpers: heading trails and paragraph grouping with line locators."""

from app.services.rules.knowledge import Element


class HeadingTrail:
    """The current heading path; a heading replaces its level and everything below it."""

    def __init__(self) -> None:
        self._headings: list[tuple[int, str]] = []

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(title for _, title in self._headings)

    def enter(self, level: int, title: str) -> None:
        while self._headings and self._headings[-1][0] >= level:
            self._headings.pop()
        self._headings.append((level, title))


def paragraphs(
    lines: list[str],
    heading_path: tuple[str, ...],
    page: int | None = None,
    slide: int | None = None,
) -> list[Element]:
    """Group consecutive non-blank lines; line numbers are 1-based within `lines`."""
    groups: list[list[tuple[int, str]]] = [[]]
    for number, line in enumerate(lines, start=1):
        _place(groups, number, " ".join(line.split()))
    return [_element(group, heading_path, page, slide) for group in groups if group]


def _place(groups: list[list[tuple[int, str]]], number: int, text: str) -> None:
    if text:
        groups[-1].append((number, text))
    elif groups[-1]:
        groups.append([])


def _element(
    group: list[tuple[int, str]],
    heading_path: tuple[str, ...],
    page: int | None,
    slide: int | None,
) -> Element:
    text = "\n".join(line for _, line in group)
    return Element(text, heading_path, group[0][0], group[-1][0], page, slide)


def cells_text(values: list[str]) -> str:
    return " | ".join(value for value in values if value)
