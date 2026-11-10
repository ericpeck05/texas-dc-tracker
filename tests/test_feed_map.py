"""Feed (filings newest first) and approximate map positions."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from tracker.api import create_app
from tracker.db.models import Equipment as Eq
from tracker.db.models import (
    EquipmentCategory,
    Extraction,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.db.session import init_db, make_engine, session_scope
from tracker.locate import site_location
from tracker.views.feed import feed


def _seed(session):
    abilene = Site(name="Big Campus", county="Taylor")
    rural = Site(name="Rural Campus", county="Ward")
    nowhere = Site(name="Registry Only")
    for s in (abilene, rural, nowhere):
        s.set_status(SiteStatus.FILED)
    session.add_all([abilene, rural, nowhere])
    session.flush()
    air = Signal(
        site=abilene,
        type=SignalType.AIR_PERMIT,
        source="tceq_air",
        permit_number="177263",
        title="Big Campus permit",
        filing_date=date(2026, 11, 1),
    )
    storm = Signal(
        site=rural,
        type=SignalType.STORMWATER,
        source="tceq_central_registry",
        title="stormwater",
        filing_date=date(2026, 6, 1),
    )
    old = Signal(
        site=nowhere,
        type=SignalType.INCENTIVE,
        source="comptroller_dc_registry",
        title="Registry Only",
        filing_date=date(2020, 1, 1),
    )
    session.add_all([air, storm, old])
    session.flush()
    session.add(
        Extraction(
            signal=air, extractor="x", extractor_version="1", fields={"nearest_city": "Abilene"}
        )
    )
    session.add(
        Eq(
            signal=air,
            site=abilene,
            category=EquipmentCategory.DIESEL_GENERATOR,
            count=4,
            unit_size=2.0,
            unit="MW",
        )
    )
    session.flush()
    return abilene, rural, nowhere


def test_feed_and_locations(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        abilene, rural, nowhere = _seed(session)
        recent = feed(session, days=30, today=date(2026, 11, 9))
        year = feed(session, days=365, today=date(2026, 11, 9))
        storm_only = feed(session, days=None, signal_type="stormwater")
        loc = {s.name: site_location(s, list(s.signals)) for s in (abilene, rural, nowhere)}

    assert [i["title"] for i in recent["items"]] == ["Big Campus permit"]
    item = recent["items"][0]
    assert item["equipment"] == {"generators": 4, "turbines": 0, "mw": 8.0}
    assert item["site"]["name"] == "Big Campus"
    assert [i["title"] for i in year["items"]] == ["Big Campus permit", "stormwater"]
    assert [i["type"] for i in storm_only["items"]] == ["stormwater"]

    assert loc["Big Campus"]["located"] == "city" and loc["Big Campus"]["place"] == "Abilene"
    assert abs(loc["Big Campus"]["lat"] - 32.45) < 0.2
    assert loc["Rural Campus"]["located"] == "county"
    assert loc["Registry Only"]["located"] is None

    client = TestClient(create_app(engine, web_dist=None))
    body = client.get("/api/feed?days=0").json()
    assert len(body["items"]) == 3 and "Not investment advice" in body["disclaimer"]
    sites = {r["name"]: r for r in client.get("/api/sites").json()["sites"]}
    assert sites["Rural Campus"]["place"] == "Ward County"
