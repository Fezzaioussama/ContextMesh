"""HTML documents and crawled pages: headings, text blocks with source lines, links."""

from bs4 import BeautifulSoup, Tag

from app.domain.knowledge import Element
from app.domain.web import resolved_link
from app.parsers.elements import HeadingTrail
from app.services.ports.web import FetchedPage, PageSummary

REMOVED = ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "form"]
HEADINGS = {f"h{level}": level for level in range(1, 7)}
BLOCKS = frozenset(
    {*HEADINGS, "p", "li", "pre", "blockquote", "td", "th", "dd", "dt", "figcaption", "caption"}
)


def _soup(content: bytes) -> BeautifulSoup:
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(REMOVED):
        tag.decompose()
    return soup


class HtmlParser:
    """Lines are the block's starting line in the original HTML source."""

    def parse(self, content: bytes) -> tuple[Element, ...]:
        soup = _soup(content)
        collector = _Collector()
        for tag in _outermost_blocks(soup.body or soup):
            collector.add(tag)
        return tuple(collector.elements)


class _Collector:
    def __init__(self) -> None:
        self._trail = HeadingTrail()
        self.elements: list[Element] = []

    def add(self, tag: Tag) -> None:
        text = _text(tag)
        if not text:
            return
        if tag.name in HEADINGS:
            self._trail.enter(HEADINGS[tag.name], text)
            return
        line = tag.sourceline or 1
        self.elements.append(Element(text, self._trail.path, line, line))


def _outermost_blocks(root: Tag) -> list[Tag]:
    """Blocks with no block ancestor, in document order, in linear time.

    A block is a known block element, or a `div` with no known block element below it
    (a leaf `div` holding loose text). Ancestor checks per tag would be quadratic.
    """
    holders = _block_holders(root)
    found: list[Tag] = []
    pending = [root]
    while pending:
        tag = pending.pop()
        if _is_block(tag, holders):
            found.append(tag)
        else:
            pending.extend(reversed(_child_tags(tag)))
    return found


def _block_holders(root: Tag) -> set[int]:
    """Ids of tags with a known block element below them, in one bottom-up pass."""
    holders: set[int] = set()
    for tag in reversed(root.find_all(True)):
        if tag.name in BLOCKS or id(tag) in holders:
            holders.add(id(tag.parent))
    return holders


def _is_block(tag: Tag, holders: set[int]) -> bool:
    return tag.name in BLOCKS or (tag.name == "div" and id(tag) not in holders)


def _child_tags(tag: Tag) -> list[Tag]:
    return [child for child in tag.children if isinstance(child, Tag)]


def _text(tag: Tag) -> str:
    if tag.name == "pre":
        return tag.get_text().strip()
    return " ".join(tag.get_text().split())


class HtmlPageReader:
    """Titles and in-page links for the crawler; non-HTML pages have neither."""

    def summarize(self, page: FetchedPage) -> PageSummary:
        if page.media_type != "text/html":
            return PageSummary(None, ())
        soup = BeautifulSoup(page.content, "html.parser")
        return PageSummary(_title(soup), _links(soup, page.url))


def _title(soup: BeautifulSoup) -> str | None:
    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    return title or None


def _links(soup: BeautifulSoup, base: str) -> tuple[str, ...]:
    links = (resolved_link(base, anchor["href"]) for anchor in soup.find_all("a", href=True))
    return tuple(dict.fromkeys(link for link in links if link))
