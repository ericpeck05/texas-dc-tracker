"""Text extraction from raw documents. Pages are separated by form feeds (\\f)."""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger(__name__)

PAGE_BREAK = "\f"


def pdf_to_text(path: Path) -> tuple[str, str]:
    """Return (text, method). Tries pdfplumber, falls back to PyMuPDF."""
    text = ""
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            text = PAGE_BREAK.join((page.extract_text() or "") for page in pdf.pages)
        if text.strip():
            return text, "pdfplumber"
    except Exception as exc:  # malformed PDFs are common; fall through to PyMuPDF
        log.warning("pdfplumber failed on %s: %s", path, exc)

    import pymupdf

    with pymupdf.open(path) as doc:
        text = PAGE_BREAK.join(page.get_text() for page in doc)
    return text, "pymupdf"


class _TextCollector(HTMLParser):
    _SKIP = {"script", "style", "noscript"}
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:  # noqa: ARG002
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    collector = _TextCollector()
    collector.feed(html)
    return "".join(collector.parts)


def document_to_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_to_text(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return html_to_text(raw), "html"
    return raw, "text"
