"""Chapter 312 abatements: parsing, data center filter, and site matching."""

from __future__ import annotations

import csv
import io

from sqlalchemy import select

from tracker.abatement_import import import_abatements
from tracker.collectors.comptroller.abatements import data_center_reason, parse_detail
from tracker.db.models import (
    Confidence,
    Document,
    Link,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, link_party

HEADER = [
    "Record ID",
    "New Record ID Linked to Older Record ID",
    "CAD Name (Reporting Entity)",
    "Lead Taxing Unit Name",
    "Reinvestment Zone Name",
    "Abatement Status",
    "Taxing Unit Name",
    "Property Owner Name",
    "NAICS Code",
    "Executed/Signed Date of Agreement",
    "Expiration Date of Agreement",
    "Base Value of Abated Property",
    "Authorized User Name",
]


def _csv(rows: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=HEADER)
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in HEADER})
    return out.getvalue().encode()


def _row(rid, owner, *, cad="Ellis CAD", lead="Ellis County", zone="Zone 1", naics="", **kw):
    base = {
        "Record ID": rid,
        "CAD Name (Reporting Entity)": cad,
        "Lead Taxing Unit Name": lead,
        "Reinvestment Zone Name": zone,
        "Abatement Status": "Active",
        "Property Owner Name": owner,
        "Executed/Signed Date of Agreement": "05/22/2024",
        "Authorized User Name": "Filer Name Here",
    }
    # The file repeats each agreement once per taxing unit, plus a blank-unit row.
    return [
        {**base, **kw, "Taxing Unit Name": lead, "NAICS Code": naics},
        {**base, **kw, "Taxing Unit Name": "", "NAICS Code": ""},
    ]


def test_parse_groups_rows_and_drops_people_and_governments():
    content = _csv(
        _row("1", "City of San Antonio & Cyrusone LLC", cad="Bexar CAD", lead="San Antonio City")
        + _row("2", "Jane Q Example & John Example")
        + _row(
            "3",
            "ET Amarillo LLC & Amazon.com Services LLC",
            cad="Potter Randall CAD",
            lead="Amarillo City",
        )
        + [{**_row("3", "x")[0], "Taxing Unit Name": "Potter County", "Property Owner Name": ""}]
    )
    by_id = {a.record_id: a for a in parse_detail(content)}
    assert by_id["1"].owners == ["Cyrusone LLC"]
    assert by_id["1"].county == "Bexar"
    assert by_id["2"].owners == [] and by_id["2"].people_omitted == 2
    assert by_id["3"].owners == ["ET Amarillo LLC", "Amazon.com Services LLC"]
    assert by_id["3"].county == "Potter"
    assert "Filer Name Here" not in str(by_id["1"].as_row())


def test_data_center_filter():
    abs_ = {
        a.record_id: a
        for a in parse_detail(
            _csv(
                _row("1", "Felix 1, LLC", naics="518210")
                + _row("2", "Some Holdings LLC", zone="Big Data Center Project")
                + _row("3", "Sharka LLC")
                + _row("4", "Amazon.com Services LLC")
                + _row("5", "Crusoe Energy System, LLC")
                + _row("6", "Tractor Supply Company")
            )
        )
    }
    known = {"sharka", "amazon com services"}
    assert "NAICS" in data_center_reason(abs_["1"], known)
    assert data_center_reason(abs_["2"], known) == "named as a data center"
    assert "already tied" in data_center_reason(abs_["3"], known)
    assert data_center_reason(abs_["4"], known) is None  # Amazon also builds warehouses
    assert "Crusoe" in data_center_reason(abs_["5"], known)
    assert data_center_reason(abs_["6"], known) is None


def _setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        book = EntityBook(session)
        for name, owner in [
            ("Sharka Data Center", "Sharka LLC"),
            ("Cyrusone LLC - Houston West III", "Cyrusone LLC"),
            ("San Antonio Data Center II", "Cyrusone LLC"),
            ("Riot Corsicana I", "Riot Platforms Inc"),
            ("Riot Corsicana 02", "Riot Platforms Inc"),
        ]:
            site = Site(name=name)
            site.set_status(SiteStatus.FILED)
            session.add(site)
            session.flush()
            link_party(
                session,
                book,
                owner,
                site,
                LinkType.OWNS,
                Confidence.HIGH,
                evidence_url=None,
                note="t",
            )
    return engine


def _import(engine, content):
    with session_scope(engine) as session:
        sha = str(hash(content))
        doc = session.scalar(select(Document).where(Document.sha256 == sha))
        if doc is None:
            doc = Document(
                source="comptroller_ch312", url="u", sha256=sha, local_path="x", size_bytes=1
            )
            session.add(doc)
            session.flush()
        report = import_abatements(session, parse_detail(content), doc)
        return {
            m.abatement.record_id: (m.how, m.site.name if m.site else None) for m in report.new
        }, report


def test_matching(tmp_path):
    engine = _setup(tmp_path)
    content = _csv(
        _row("10", "Sharka LLC", zone="Midlothian Zone 14")
        + _row("11", "Cyrusone LLC", cad="Fort Bend CAD", lead="Houston City")
        + _row("12", "Riot Platforms Inc", cad="Navarro CAD", lead="Navarro County")
        + _row(
            "13",
            "Crusoe Energy System, LLC",
            cad="Wilbarger CAD",
            lead="Wilbarger County",
            zone="Crusoe Data Center Project",
        )
    )
    got, report = _import(engine, content)
    assert got["10"] == ("owner", "Sharka Data Center")
    assert got["11"] == ("owner+place", "Cyrusone LLC - Houston West III")
    assert got["12"] == ("review", None)
    assert got["13"] == ("new", "Crusoe Data Center Project (Wilbarger County)")
    assert len(report.review) == 2

    with session_scope(engine) as session:
        sharka = session.scalar(select(Site).where(Site.name == "Sharka Data Center"))
        assert sharka.county == "Ellis"
        assert sharka.status == SiteStatus.APPROVED
        types = {s.type for s in sharka.signals}
        assert types == {SignalType.APPROVAL}
        houston = session.scalar(select(Site).where(Site.name.like("Cyrusone%")))
        assert houston.county == "Fort Bend"
        crusoe = session.scalar(select(Site).where(Site.name.like("Crusoe%")))
        owns = session.scalars(
            select(Link).where(
                Link.to_type == RecordType.SITE,
                Link.to_id == crusoe.id,
                Link.link_type == LinkType.OWNS,
            )
        ).all()
        assert [ln.confidence for ln in owns] == [Confidence.HIGH]

    # Same file again: nothing new. A modification that replaces record 10 joins its site.
    got, report = _import(engine, content)
    assert got == {} and report.unchanged == 4
    got, _ = _import(
        engine,
        _csv(
            _row(
                "20",
                "Sharka LLC",
                **{"New Record ID Linked to Older Record ID": "10"},
                naics="518210",
            )
        ),
    )
    assert got["20"] == ("record", "Sharka Data Center")
    with session_scope(engine) as session:
        assert session.scalar(select(Signal).where(Signal.permit_number == "20")).site_id
