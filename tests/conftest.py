from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tracker.db.session import init_db, make_engine, session_scope


@pytest.fixture
def engine():
    eng = make_engine("sqlite:///:memory:")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    with session_scope(engine) as s:
        yield s


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Write a simple text PDF, one string per page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def pdf_factory(tmp_path):
    def _make(pages: list[str], name: str = "permit.pdf") -> Path:
        return make_pdf(tmp_path / name, pages)

    return _make
