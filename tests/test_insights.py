"""Overview: who is building and how fast."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from tracker.api import create_app
from tracker.db.models import Confidence, EquipmentCategory, LinkType, Signal, SignalType, Site
from tracker.db.models import Equipment as Eq
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, link_party
from tracker.views.insights import insights, quarters

TODAY = date(2026, 11, 10)
EV = {"evidence_url": "https://example.test/doc", "note": "test"}


def _site(session, name, county, filed, kind=SignalType.STORMWATER):
    site = Site(name=name, county=county)
    session.add(site)
    sig = Signal(site=site, type=kind, source="x", title=name, filing_date=filed)
    session.add(sig)
    session.flush()
    return site, sig


def _seed(session):
    book = EntityBook(session)
    a, air = _site(session, "Alpha", "Bexar", date(2026, 3, 2), SignalType.AIR_PERMIT)
    session.add(
        Eq(
            signal=air,
            site=a,
            category=EquipmentCategory.DIESEL_GENERATOR,
            count=10,
            unit_size=2.0,
            unit="MW",
        )
    )
    b, _ = _site(session, "Bravo", "Bexar", date(2025, 8, 1))
    c, _ = _site(session, "Charlie", "Ellis", date(2024, 1, 5))
    d, _ = _site(session, "Delta", None, date(2026, 9, 9))
    session.add(Site(name="No Filings"))
    for site in (a, b):
        link_party(session, book, "Google LLC", site, LinkType.OCCUPIES, Confidence.HIGH, **EV)
    link_party(
        session, book, "Google LLC", c, LinkType.OCCUPIES, Confidence.LOW, **EV
    )  # not counted
    link_party(session, book, "Paile LLC", d, LinkType.OPERATES, Confidence.MEDIUM, **EV)
    session.flush()


def test_quarters_run_to_today():
    assert quarters(2026, TODAY) == ["2026 Q1", "2026 Q2", "2026 Q3", "2026 Q4"]


def test_overview_counts_companies_pace_and_places(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        _seed(session)
        view = insights(session, today=TODAY)

    t = view["totals"]
    assert (t["sites"], t["named"], t["new_12m"], t["new_prior_12m"]) == (4, 2, 2, 1)
    assert (t["mw"], t["units"], t["unit_sites"]) == (20.0, 10, 1)

    (google,) = view["companies"]
    assert google["company"] == "Alphabet (Google)"
    assert [s["name"] for s in google["sites"]] == ["Alpha", "Bravo"]  # largest first
    assert (google["new_12m"], google["mw"], google["first"]) == (1, 20.0, "2025-08-01")
    assert [o["entity"] for o in view["other_names"]] == ["Paile LLC"]

    pace = {r["quarter"]: r for r in view["pace"]}
    assert pace["2026 Q1"]["new_sites"] == 1 and pace["2026 Q1"]["air_permit"] == 1
    assert pace["2026 Q1"]["units"] == 10 and pace["2026 Q1"]["mw"] == 20.0
    assert pace["2026 Q4"]["partial"] and "partial" not in pace["2026 Q3"]
    assert view["counties"][0] == {"county": "Bexar", "sites": 2, "new_12m": 1, "mw": 20.0}
    assert view["newest"][0]["name"] == "Delta"
    assert view["takeaways"][0].startswith("2 sites had a first public filing in 2026 so far")

    client = TestClient(create_app(engine, web_dist=None))
    body = client.get("/api/overview").json()
    assert "Not investment advice" in body["disclaimer"] and body["totals"]["sites"] == 4
