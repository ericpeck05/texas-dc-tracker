"""Review queue: confirm and reject low links, merges, filings, and replay after a rebuild."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from tracker.api import create_app
from tracker.db.models import (
    Confidence,
    Extraction,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    ReviewDecision,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, add_link, link_party
from tracker.review import apply_decisions, read_decisions, review_queue
from tracker.views.sites import site_view
from tracker.views.ticker import ticker_view

URL = "https://records.example/doc"


def _low(session, ft, fi, tt, ti, link_type, note):
    add_link(
        session,
        from_type=ft,
        from_id=fi,
        to_type=tt,
        to_id=ti,
        link_type=link_type,
        method=LinkMethod.ADDRESS_MATCH
        if link_type != LinkType.SAME_SITE
        else LinkMethod.NAME_MATCH,
        confidence=Confidence.LOW,
        note=note,
    )


def _seed(session) -> dict[str, int]:
    """Two sites that may be one, an abatement with two candidate sites, a maybe-parent."""
    book = EntityBook(session)
    old = Site(name="Old Campus", county="Ellis", tceq_rns=["RN100000001"])
    old.set_status(SiteStatus.FILED)
    new = Site(name="New Campus", tceq_rns=["RN100000002"])
    new.set_status(SiteStatus.UNDER_CONSTRUCTION)
    a = Site(name="Riot A", county="Milam")
    b = Site(name="Riot B")
    for s in (a, b):
        s.set_status(SiteStatus.FILED)
    session.add_all([old, new, a, b])
    session.flush()
    for site, n in ((old, 2), (new, 1), (a, 1), (b, 1)):
        for i in range(n):
            session.add(
                Signal(
                    site=site,
                    type=SignalType.STORMWATER,
                    source="tceq_central_registry",
                    permit_number=f"TXR15{site.id}{i}",
                )
            )
    abatement = Signal(
        type=SignalType.APPROVAL,
        source="comptroller_ch312",
        permit_number="000015041",
        title="Tax abatement: Navarro zone",
    )
    session.add(abatement)
    session.flush()
    session.add(
        Extraction(
            signal=abatement,
            extractor="x",
            extractor_version="1",
            fields={"county": "Navarro", "owners": ["Riot Platforms, Inc."], "url": URL},
        )
    )
    _low(session, RecordType.SITE, new.id, RecordType.SITE, old.id, LinkType.SAME_SITE, "names")
    for site in (a, b):
        _low(
            session,
            RecordType.SIGNAL,
            abatement.id,
            RecordType.SITE,
            site.id,
            LinkType.SAME_SITE,
            "owner has several sites",
        )
    entity = link_party(
        session, book, "Sharka LLC", old, LinkType.OWNS, Confidence.HIGH, evidence_url=URL, note="o"
    )
    google = link_party(
        session,
        book,
        "Google LLC",
        a,
        LinkType.OCCUPIES,
        Confidence.HIGH,
        evidence_url=URL,
        note="t",
    )
    company_id = session.scalar(
        select(Link.from_id).where(
            Link.to_type == RecordType.ENTITY,
            Link.to_id == google.id,
            Link.from_type == RecordType.COMPANY,
        )
    )
    _low(
        session,
        RecordType.COMPANY,
        company_id,
        RecordType.ENTITY,
        entity.id,
        LinkType.PARENT_OF,
        "same mailing address",
    )
    session.flush()
    return {"old": old.id, "new": new.id, "a": a.id, "b": b.id, "abatement": abatement.id}


def _setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        ids = _seed(session)
    return engine, ids


def _ids(items, kind):
    return [i["link_id"] for i in items if i["kind"] == kind]


def test_queue_confirm_and_reject(tmp_path):
    engine, ids = _setup(tmp_path)
    path = tmp_path / "decisions.csv"
    client = TestClient(create_app(engine, web_dist=None, decisions_path=path))

    body = client.get("/api/review").json()
    assert body["counts"] == {"same_site": 1, "filing": 2, "parent": 1, "affiliate": 0}
    parent = next(i for i in body["items"] if i["kind"] == "parent")
    assert parent["left"]["kind"] == "company" and parent["left"]["ticker"] == "GOOGL"
    assert parent["right"]["name"] == "Sharka LLC"
    assert client.get("/api/review?kind=nope").status_code == 400

    # Before review, the low parent link gives Alphabet a low tie to Old Campus.
    with session_scope(engine) as session:
        demand = ticker_view(session, "GOOGL")["demand"]
        sites = {s["name"] for s in demand["sites"] + demand["leads"]}
    assert "Old Campus" in sites

    # Reject it: gone from the ticker and the site page.
    r = client.post(f"/api/review/{parent['link_id']}", json={"decision": "reject"})
    assert r.status_code == 200
    with session_scope(engine) as session:
        demand = ticker_view(session, "GOOGL")["demand"]
        sites = {s["name"] for s in demand["sites"] + demand["leads"]}
        chain = site_view(session, ids["old"])["owner_chain"]
    assert "Old Campus" not in sites
    assert chain[0]["companies"] == []
    again = client.post(f"/api/review/{parent['link_id']}", json={"decision": "reject"})
    assert again.status_code == 409

    # Confirm the same-site link: the site with fewer signals merges into the other.
    same = _ids(body["items"], "same_site")[0]
    r = client.post(f"/api/review/{same}", json={"decision": "confirm", "note": "same RN area"})
    assert r.json()["site_id"] == ids["old"]
    with session_scope(engine) as session:
        old = session.get(Site, ids["old"])
        assert old.aliases == ["New Campus"]
        assert old.tceq_rns == ["RN100000001", "RN100000002"]
        assert old.status == SiteStatus.UNDER_CONSTRUCTION
        assert len(old.signals) == 3
        manual = session.scalar(select(Link).where(Link.method == LinkMethod.MANUAL))
        assert manual.confidence == Confidence.HIGH and "same RN area" in manual.note
        assert site_view(session, ids["new"])["merged_into"]["id"] == ids["old"]

    # Confirm one filing candidate: it moves there, the other candidate is rejected.
    filing = _ids(body["items"], "filing")[1]  # Riot B
    client.post(f"/api/review/{filing}", json={"decision": "confirm"})
    with session_scope(engine) as session:
        sig = session.get(Signal, ids["abatement"])
        assert sig.site_id == ids["b"]
        b = session.get(Site, ids["b"])
        assert b.status == SiteStatus.APPROVED and b.county == "Navarro"
        view = site_view(session, ids["b"])
        assert view["owner_chain"][0]["entity"] == "Riot Platforms, Inc."
        assert session.query(ReviewDecision).count() == 4  # 3 by hand, 1 automatic
    assert client.get("/api/review").json()["counts"] == {
        "same_site": 0,
        "filing": 0,
        "parent": 0,
        "affiliate": 0,
    }

    rows = read_decisions(path)
    assert [(r.decision, r.kind) for r in rows] == [
        ("reject", "parent"),
        ("confirm", "same_site"),
        ("confirm", "filing"),
    ]
    assert rows[1].from_ref == "rn:RN100000002" and rows[1].to_ref == "rn:RN100000001"
    assert "Agent" not in json.dumps(body)


def test_decisions_replay_on_a_fresh_database(tmp_path):
    engine, _ = _setup(tmp_path)
    path = tmp_path / "decisions.csv"
    client = TestClient(create_app(engine, web_dist=None, decisions_path=path))
    items = client.get("/api/review").json()["items"]
    for item in items:
        decision = "confirm" if item["kind"] in ("same_site", "filing") else "reject"
        if item["kind"] == "filing" and item["right"]["name"] == "Riot A":
            continue
        client.post(f"/api/review/{item['link_id']}", json={"decision": decision})

    fresh = make_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_db(fresh)
    with session_scope(fresh) as session:
        ids = _seed(session)
        missing = apply_decisions(session, read_decisions(path))
        assert missing == []
        assert review_queue(session)["counts"]["filing"] == 0
        assert session.get(Signal, ids["abatement"]).site_id == ids["b"]
        assert session.get(Site, ids["old"]).aliases == ["New Campus"]
