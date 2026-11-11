"""Ticker page data and API: demand-side sites, supplied equipment, leads, evidence."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tracker.api import create_app
from tracker.db.models import (
    Company,
    CompanyRole,
    Confidence,
    Document,
    EquipmentCategory,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
)
from tracker.db.models import Equipment as Eq
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, add_link, get_company, link_party
from tracker.views.ticker import ticker_index, ticker_view

URL = "https://records.example/doc1"


def _site_with_permit(session, name, holder, rows):
    site = Site(name=name, county="Bexar")
    doc = Document(
        source="tceq_air", url=URL, sha256=name.ljust(64, "0"), local_path="p", size_bytes=1
    )
    sig = Signal(
        site=site, document=doc, type=SignalType.AIR_PERMIT, source="tceq_air", permit_number="1001"
    )
    session.add_all([site, doc, sig])
    session.flush()
    for category, count, size, unit, make, model in rows:
        session.add(
            Eq(
                signal=sig,
                site=site,
                category=category,
                count=count,
                unit_size=size,
                unit=unit,
                manufacturer=make,
                model=model,
                page=3,
                snippet="s",
            )
        )
    link_party(
        session,
        EntityBook(session),
        holder,
        site,
        LinkType.FILED_BY,
        Confidence.HIGH,
        evidence_url=URL,
        note="applicant",
    )
    session.flush()
    return site


def _seed(session):
    _site_with_permit(
        session,
        "Microsoft Test DC",
        "Microsoft Corporation",
        [
            (EquipmentCategory.DIESEL_GENERATOR, 10, 3000, "kW", "Caterpillar", "3516C"),
            (EquipmentCategory.DIESEL_GENERATOR, 4, None, None, "Cummins", "QSK60"),
        ],
    )
    _site_with_permit(
        session,
        "Turbine Site",
        "Example Power LLC",
        [(EquipmentCategory.GAS_TURBINE, 5, 38, "MW", "Solar Turbines", "Titan 350")],
    )
    # A low-confidence lead: only an address clue ties Microsoft to this LLC.
    lead = Site(name="Maybe Microsoft")
    session.add(lead)
    session.flush()
    entity = link_party(
        session,
        EntityBook(session),
        "Quiet Acres LLC",
        lead,
        LinkType.OWNS,
        Confidence.HIGH,
        evidence_url=URL,
        note="registry",
    )
    msft = session.query(Company).filter_by(name="Microsoft").one()
    add_link(
        session,
        from_type=RecordType.COMPANY,
        from_id=msft.id,
        to_type=RecordType.ENTITY,
        to_id=entity.id,
        link_type=LinkType.PARENT_OF,
        method=LinkMethod.ADDRESS_MATCH,
        confidence=Confidence.LOW,
        note="address",
    )
    session.flush()


def test_demand_side_counts_and_evidence(session):
    _seed(session)
    view = ticker_view(session, "msft")
    totals = view["demand"]["totals"]
    assert (totals["sites"], totals["generators"], totals["mw"], totals["unsized"]) == (
        1,
        14,
        30.0,
        4,
    )
    (site,) = view["demand"]["sites"]
    assert site["confidence"] == "high" and site["power"]["sources"][0]["url"] == URL
    (tie,) = site["ties"]
    assert (tie["role"], tie["entity"]) == ("permit holder", "Microsoft Corporation")
    assert tie["entity_to_site"]["evidence_url"] == URL
    # The lead is listed apart and not in the totals.
    assert [s["name"] for s in view["demand"]["leads"]] == ["Maybe Microsoft"]


def test_supply_side_rolls_brands_up_to_the_parent_ticker(session):
    _seed(session)
    view = ticker_view(session, "CAT")  # Caterpillar generators + Solar Turbines turbines
    assert view["supply"]["makers"] == ["Caterpillar", "Perkins", "Solar Turbines"]
    totals = view["supply"]["totals"]
    assert (totals["generators"], totals["turbines"], totals["mw"]) == (10, 5, 220.0)
    unit = view["supply"]["sites"][0]["units"][0]
    assert unit["source"]["page"] == 3 and unit["source"]["url"] == URL


def test_index_and_unknown_ticker(session):
    _seed(session)
    symbols = [r["symbol"] for r in ticker_index(session)]
    assert {"MSFT", "CAT", "CMI"} <= set(symbols)
    assert ticker_view(session, "ZZZZ") is None


def test_api(tmp_path):
    # A file database: the test client runs requests on another thread.
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        _seed(session)
    client = TestClient(create_app(engine, web_dist=None))
    body = client.get("/api/tickers/MSFT").json()
    assert body["symbol"] == "MSFT" and "Not investment advice" in body["disclaimer"]
    assert client.get("/api/tickers/ZZZZ").status_code == 404
    assert any(r["symbol"] == "CAT" for r in client.get("/api/tickers").json()["tickers"])


def test_company_without_sites_has_no_page_entry(session):
    get_company(session, "Iron Mountain", CompanyRole.REIT, "IRM", "NYSE")
    session.flush()
    assert "IRM" not in [r["symbol"] for r in ticker_index(session)]
    assert session.query(Link).count() == 0
