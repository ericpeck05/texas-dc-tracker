"""SEC EDGAR mentions: which names are searched, verification, and matching."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from tracker.collectors.sec.edgar import (
    MissingContact,
    Query,
    _company,
    evidence,
    search_phrase,
    sec_settings,
    text_of,
)
from tracker.config import Settings
from tracker.db.models import Confidence, Document, Link, Signal, SignalType, Site, SiteStatus
from tracker.db.session import init_db, make_engine, session_scope
from tracker.sec_import import import_sec, site_queries


def test_only_distinctive_site_names_are_searched():
    assert search_phrase("Longhorn Data Center") == "Longhorn Data Center"
    assert search_phrase("Felix 1 (Wilbarger County)") == "Felix 1"
    assert search_phrase("Data Center 1") is None
    assert search_phrase("Dfw1") is None
    assert search_phrase("Sharka Holdings LLC") is None
    assert search_phrase("NTT Global Data Centers Americas") is None  # a company's name


def test_contact_is_required_and_goes_in_the_user_agent():
    with pytest.raises(MissingContact):
        sec_settings(replace(Settings(), sec_contact=None))
    s = sec_settings(replace(Settings(), sec_contact="someone@example.com"))
    assert s.user_agent.endswith("someone@example.com")
    assert "example.com" not in Settings(sec_contact=None).user_agent


def test_evidence_needs_the_name_and_texas_or_the_place():
    doc = text_of("<p>We signed a lease at the <b>Longhorn Data Center</b> near Abilene.</p>")
    assert "Longhorn Data Center" in evidence(doc, "Longhorn Data Center", ["Taylor", "Abilene"])
    assert evidence(doc, "Longhorn Data Center", ["Ward"]) is None  # no Texas, wrong place
    assert evidence(doc, "Goodnight Data Center", ["Taylor"]) is None
    assert _company(["NUSCALE POWER Corp  (SMR, SMR-WT)  (CIK 0001822966)"]) == (
        "NUSCALE POWER Corp",
        "SMR",
    )


def _hit(phrase, adsh="0000000000-26-000001"):
    return {
        "phrase": phrase,
        "adsh": adsh,
        "cik": "1",
        "company": "Example Corp",
        "ticker": "EXM",
        "form": "8-K",
        "file_type": "EX-99.1",
        "file_date": "2026-10-01",
        "url": "https://www.sec.gov/Archives/edgar/data/1/x/doc.htm",
        "snippet": f"... {phrase} in Texas ...",
    }


def test_import_matches_one_site_and_reviews_shared_names(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        longhorn = Site(name="Longhorn Data Center", county="Taylor")
        a = Site(name="Alamo Data Center", county="Bexar")
        b = Site(name="Plano Campus", aliases=["Alamo Data Center"])
        for s in (longhorn, a, b):
            s.set_status(SiteStatus.FILED)
            session.add(s)
        session.flush()
        for s in (longhorn, a, b):
            session.add(Signal(site=s, type=SignalType.INCENTIVE, source="x", title="t"))
        doc = Document(source="sec_edgar", url="u", sha256="s" * 64, local_path="x", size_bytes=1)
        session.add(doc)
        session.flush()

        queries = site_queries(session)
        assert [q.phrase for q in queries] == ["Longhorn Data Center", "Alamo Data Center"]
        assert queries[0] == Query("Longhorn Data Center", ["Taylor"])

        report = import_sec(session, [_hit("Longhorn Data Center"), _hit("Alamo Data Center")], doc)
        assert report.matched == [("Longhorn Data Center", "Example Corp", "Longhorn Data Center")]
        assert len(report.review) == 2
        sig = session.scalar(select(Signal).where(Signal.title.like("%Longhorn%")))
        assert sig.type == SignalType.SEC_MENTION and sig.site_id == longhorn.id
        link = session.scalar(select(Link).where(Link.from_id == sig.id))
        assert link.confidence == Confidence.MEDIUM
        again = import_sec(session, [_hit("Longhorn Data Center")], doc)
        assert again.new_signals == 0

        # The filer's ticker page lists the site it names, apart from any role, and not the
        # low-confidence shared-name candidates.
        from tracker.views.ticker import ticker_index, ticker_view

        view = ticker_view(session, "exm")
        assert view["names"] == ["Example Corp"]
        assert [r["name"] for r in view["sec_mentions"]] == ["Longhorn Data Center"]
        assert view["sec_mentions"][0]["filings"][0]["form"] == "8-K"
        assert view["demand"]["totals"]["sites"] == 0
        assert [(r["symbol"], r["sec_named_sites"]) for r in ticker_index(session)] == [("EXM", 1)]
