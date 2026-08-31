"""The only BeautifulSoup code in the pipeline.

Takes HTML plus the content selector from the active section config and
returns the extracted container. Raises on a zero match rather than silently
returning the whole page — an untransformed doc that looks fine is the worst
outcome.
"""

from bs4 import BeautifulSoup


class ContentExtractionError(Exception):
    pass


def extract_content(html: str, content_selector: str, source_url: str = "") -> str:
    """Return the first node matching ``content_selector`` as a standalone
    HTML fragment. Zero matches raise; an empty-text match raises too — a
    present-but-hollow container is the same silent failure."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(content_selector)
    if node is None:
        raise ContentExtractionError(
            f"content_container {content_selector!r} matched nothing at {source_url or '<html>'}"
        )
    if not node.get_text(strip=True):
        raise ContentExtractionError(
            f"content_container {content_selector!r} matched an empty node "
            f"at {source_url or '<html>'}"
        )
    return str(node)
