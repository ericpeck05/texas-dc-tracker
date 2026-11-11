"""Site page and site list: owner chain, timeline, equipment, review links, privacy."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tracker.api import create_app
from tracker.db.models import (
    Confidence,
    Document,
    Entity,
    EquipmentCategory,
    Extraction,
    LinkMethod,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.db.models import Equipment as Eq
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, add_link, link_party
from tracker.views.sites import site_list, site_view

URL = "https://records.example/doc1"


def _seed(session) -> tuple[int, int]:
    book = EntityBook(session)
    site = Site(name="Test Campus", county="Ellis", tceq_rns=["RN100000001"])
    site.set_status(SiteStatus.FILED, note="created")
    other = Site(name="Maybe Same Campus")
    other.set_status(SiteStatus.FILED)
    doc = Document(source="tceq_air", url=URL, sha256="a" * 64, local_path="p", size_bytes=1)
    air = Signal(
        site=site,
        document=doc,
        type=SignalType.AIR_PERMIT,
        source="tceq_air",
        permit_number="190001",
        title=URL,
    )
    session.add_all([site, other, doc, air])
    session.flush()
    session.add(
        Extraction(
            signal=air,
            extractor="x",
            extractor_version="1",
            fields={"document_kind": "nsr_technical_review"},
        )
    )
    session.add(
        Eq(
            signal=air,
            site=site,
            category=EquipmentCategory.DIESEL_GENERATOR,
            count=8,
            unit_size=2.5,
            unit="MW",
            manufacturer="Caterpillar",
            model="3516C",
            page=4,
            snippet="eight 2.5 MW generators",
        )
    )
    reg = Signal(
        site=site,
        type=SignalType.INCENTIVE,
        source="comptroller_dc_registry",
        permit_number="LD000001",
        title="Test Campus",
    )
    session.add(reg)
    session.flush()
    add_link(
        session,
        from_type=RecordType.SIGNAL,
        from_id=reg.id,
        to_type=RecordType.SITE,
        to_id=site.id,
        link_type=LinkType.SAME_SITE,
        method=LinkMethod.NAME_MATCH,
        confidence=Confidence.MEDIUM,
        note="name matches",
    )
    owner = link_party(
        session,
        book,
        "Google LLC",
        site,
        LinkType.OCCUPIES,
        Confidence.HIGH,
        evidence_url=URL,
        note="occupant",
    )
    llc = link_party(
        session,
        book,
        "Sharka LLC",
        site,
        LinkType.OWNS,
        Confidence.MEDIUM,
        evidence_url=URL,
        note="owner",
    )
    sister = book.get("Sharka Two LLC")
    add_link(
        session,
        from_type=RecordType.ENTITY,
        from_id=llc.id,
        to_type=RecordType.ENTITY,
        to_id=sister.id,
        link_type=LinkType.AFFILIATE_OF,
        method=LinkMethod.ADDRESS_MATCH,
        confidence=Confidence.LOW,
        note="same address",
    )
    # Private details that must never reach the app.
    owner.registered_agent = "Jane Agent"
    session.get(Entity, llc.id).registered_agent = "John Agent"
    add_link(
        session,
        from_type=RecordType.SITE,
        from_id=other.id,
        to_type=RecordType.SITE,
        to_id=site.id,
        link_type=LinkType.SAME_SITE,
        method=LinkMethod.NAME_MATCH,
        confidence=Confidence.LOW,
        note="may be the same project",
    )
    session.flush()
    return site.id, other.id


def test_site_view(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        site_id, other_id = _seed(session)
        view = site_view(session, site_id)
        rows = site_list(session)

    assert view["county"] == "Ellis" and view["tceq_rns"] == ["RN100000001"]
    assert view["power"]["generators"] == 8 and view["power"]["mw"] == 20.0
    assert view["equipment"][0]["rows"][0]["page"] == 4
    titles = {s["source"]: s["title"] for s in view["signals"]}
    assert titles["tceq_air"] == "TCEQ permit 190001: nsr technical review"
    match = {s["source"]: s["match"] for s in view["signals"]}
    assert match["tceq_air"] is None and match["comptroller_dc_registry"]["confidence"] == "medium"

    chain = {e["entity"]: e for e in view["owner_chain"]}
    google = chain["Google LLC"]
    assert google["roles"][0]["role"] == "tenant"
    assert google["companies"][0]["ticker"] == "GOOGL" and google["chain_confidence"] == "high"
    assert chain["Sharka LLC"]["companies"] == []
    assert chain["Sharka LLC"]["affiliates"][0]["entity"] == "Sharka Two LLC"
    assert view["companies"] == [
        {
            "company": "Alphabet (Google)",
            "ticker": "GOOGL",
            "confidence": "high",
            "roles": ["tenant"],
        }
    ]
    assert view["review"][0]["site"] == {"id": other_id, "name": "Maybe Same Campus"}
    assert "Agent" not in json.dumps(view)

    by_name = {r["name"]: r for r in rows}
    assert by_name["Test Campus"]["signal_types"] == ["air_permit", "incentive"]
    assert by_name["Test Campus"]["sources"] == 2
    assert by_name["Test Campus"]["companies"][0]["ticker"] == "GOOGL"
    assert "Maybe Same Campus" not in by_name  # no signals, nothing to show

    client = TestClient(create_app(engine, web_dist=None))
    assert client.get(f"/api/sites/{site_id}").json()["name"] == "Test Campus"
    assert client.get("/api/sites/99999").status_code == 404
    body = client.get("/api/sites").json()
    assert "Not investment advice" in body["disclaimer"] and len(body["sites"]) == 1
