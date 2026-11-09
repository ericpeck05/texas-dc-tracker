"""Owner unmasking: franchise tax lookups, address siblings, and hand findings."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tracker.collectors.comptroller.franchise_tax import (
    Lookup,
    best_matches,
    name_matches,
    name_query_url,
    parse_rows,
)
from tracker.db.models import Confidence, Link, LinkType, RecordType, Site
from tracker.entities import EntityBook, link_party, site_companies
from tracker.owners import (
    FindingError,
    apply_findings,
    apply_lookup,
    owner_queue,
    read_findings,
)


def _row(name, number, address="PO BOX 2156", zip_code="90245", city="EL SEGUNDO"):
    return {
        "taxpayer_number": number,
        "taxpayer_name": name,
        "taxpayer_address": address,
        "taxpayer_city": city,
        "taxpayer_state": "CA",
        "taxpayer_zip": zip_code,
        "secretary_of_state_sos_or_coa_file_number": "08" + number[-8:],
        "sos_charter_date": "2024-06-04T00:00:00.000",
    }


def _site_with(session, name, holder):
    site = Site(name=name)
    session.add(site)
    session.flush()
    link_party(
        session,
        EntityBook(session),
        holder,
        site,
        LinkType.FILED_BY,
        Confidence.HIGH,
        evidence_url=None,
        note="t",
    )
    session.flush()
    return site


def test_name_matching():
    assert name_matches("Sf Houh, LLC", "SF HOUH, LLC")
    assert not name_matches("Abilene Dc 1, LLC", "ABILENE DC 10, LLC")
    # The dataset cuts names at 50 characters.
    assert name_matches(
        "Abilene Data Center Campus Master Association, Inc.",
        "ABILENE DATA CENTER CAMPUS MASTER ASSOCIATION, IN",
    )
    assert "ABILENE+DC+1%25" in name_query_url("Abilene Dc 1, LLC")
    both = parse_rows([_row("LANCIUM, INC.", "32000000001"), _row("LANCIUM LLC", "32000000002")])
    assert [t.name for t in best_matches("Lancium LLC", both)] == ["LANCIUM LLC"]


def test_lookup_fills_entity_and_sister_llcs_link(session):
    site = _site_with(session, "Hp Data Center", "Sf Houh, LLC")
    rows = [_row("SF HOUH, LLC", "32095368877"), _row("ABILENE DC 10, LLC", "32102260349")]
    status = apply_lookup(session, Lookup("name", "Sf Houh, LLC", "u", rows))
    assert status.startswith("found SF HOUH")

    (item,) = owner_queue(session)
    assert item.status == "found" and item.entity.sos_file_number == "0895368877"
    assert item.entity.address == "PO BOX 2156, EL SEGUNDO, CA 90245"

    siblings = [
        _row("SF HOUH, LLC", "32095368877"),
        _row("SF CTX1, LLC", "32111111111"),
        _row("LIBERTY TRUCKING LLC", "32122222222"),
    ]
    apply_lookup(session, Lookup("address", "PO BOX 2156|90245", "u", siblings))
    (item,) = owner_queue(session)
    assert item.affiliates == [("LIBERTY TRUCKING LLC", "low"), ("SF CTX1, LLC", "medium")]
    assert item.sites == [site]


def test_agent_office_gives_no_links(session):
    _site_with(session, "Longhorn", "Abilene Dc 1, LLC")
    apply_lookup(
        session,
        Lookup("name", "Abilene Dc 1, LLC", "u", [_row("ABILENE DC 1, LLC", "32095486604")]),
    )
    office = [_row(f"COMPANY {i} LLC", f"321{i:08d}") for i in range(40)]
    status = apply_lookup(session, Lookup("address", "PO BOX 2156|90245", "u", office))
    assert "agent office" in status
    assert not session.scalars(select(Link).where(Link.link_type == LinkType.AFFILIATE_OF)).all()


def test_sister_with_known_company_is_a_low_lead(session):
    site = _site_with(session, "Project X", "Vantage Data Centers Tx99, LLC")
    # Pretend the brand link were absent: check the borrowed one instead.
    apply_lookup(
        session,
        Lookup(
            "name",
            "Vantage Data Centers Tx99, LLC",
            "u",
            [_row("VANTAGE DATA CENTERS TX99, LLC", "32000000001", "1 MAIN ST", "75201")],
        ),
    )
    apply_lookup(
        session,
        Lookup(
            "address",
            "1 MAIN ST|75201",
            "u",
            [
                _row("VANTAGE DATA CENTERS TX99, LLC", "32000000001", "1 MAIN ST", "75201"),
                _row("VANTAGE DATA CENTERS TX98, LLC", "32000000002", "1 MAIN ST", "75201"),
            ],
        ),
    )
    borrowed = session.scalars(
        select(Link).where(
            Link.link_type == LinkType.PARENT_OF,
            Link.to_type == RecordType.ENTITY,
            Link.confidence == Confidence.LOW,
        )
    ).all()
    assert borrowed and "sister LLC" in borrowed[0].note
    # The direct brand link (medium) still wins in the site view.
    assert site_companies(session, site) == [("filed_by", "Vantage Data Centers", None, "medium")]


def test_hand_findings(session, tmp_path):
    site = _site_with(session, "Nolan Ranch", "Nolan Ranch DC Owner LLC")
    path = tmp_path / "findings.csv"
    path.write_text(
        "entity,company,ticker,exchange,role,confidence,evidence_url,note\n"
        "Nolan Ranch DC Owner LLC,Example Developer,,,developer,medium,"
        "https://example.com/filing,SOS filing names the manager\n"
        "Missing LLC,Other,,,other,low,https://example.com,\n"
    )
    missing = apply_findings(session, read_findings(path))
    assert missing == ["Missing LLC"]
    assert site_companies(session, site) == [("filed_by", "Example Developer", None, "medium")]


def test_findings_reject_people_and_missing_evidence(tmp_path):
    path = tmp_path / "findings.csv"
    path.write_text(
        "entity,company,ticker,exchange,role,confidence,evidence_url,note\n"
        "Jane Q. Smith,Example,,,other,low,https://example.com,\n"
    )
    with pytest.raises(FindingError, match="not a company"):
        read_findings(path)
    path.write_text(
        "entity,company,ticker,exchange,role,confidence,evidence_url,note\n"
        "Example LLC,Example,,,other,low,,\n"
    )
    with pytest.raises(FindingError, match="evidence_url"):
        read_findings(path)


def test_attention_lines_are_dropped_from_addresses():
    (t,) = parse_rows([_row("X LLC", "32000000001", "PO BOX 24567 ATTN: JANE DOE", "98124")])
    assert t.full_address == "PO BOX 24567, EL SEGUNDO, CA 98124"


def test_company_headquarters_address_gives_medium_link(session):
    site = _site_with(session, "Temple Data Center", "Polmer LLC")
    hq = ("1601 WILLOW RD", "94025")
    polmer = [_row("POLMER LLC", "32000000001", *hq)]
    apply_lookup(session, Lookup("name", "Polmer LLC", "u", polmer))
    rows = [
        _row("POLMER LLC", "32000000001", *hq),
        _row("META PLATFORMS TECHNOLOGIES LLC", "32000000002", *hq),
        _row("FACEBOOK PROCUREMENT, LLC", "32000000003", *hq),
        _row("SANZARU GAMES, INC.", "32000000004", *hq),
    ]
    apply_lookup(session, Lookup("address", "1601 WILLOW RD|94025", "u", rows))
    assert site_companies(session, site) == [("filed_by", "Meta", "META", "medium")]


def test_mixed_companies_at_an_address_say_nothing(session):
    from tracker.owners import address_company

    rows = parse_rows(
        [
            _row("META PLATFORMS TECHNOLOGIES LLC", "32000000002"),
            _row("FACEBOOK PROCUREMENT, LLC", "32000000003"),
            _row("ORACLE AMERICA, INC.", "32000000004"),
        ]
    )
    assert address_company(rows) is None
