"""Comptroller data center registry: parsing a trimmed copy of the real page, and import."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tracker.collectors.comptroller.data_center_registry import (
    RegistryFormatError,
    parse_registry,
)
from tracker.db.models import (
    Confidence,
    Document,
    Extraction,
    Link,
    LinkType,
    Signal,
    SignalType,
    Site,
)
from tracker.reference.companies import find_company
from tracker.registry_import import (
    entity_key,
    import_registry,
    name_tokens,
    site_companies,
    stem_tokens,
)

PAGE = (Path(__file__).parent / "fixtures" / "comptroller" / "registry_sample.html").read_text()


def _entries():
    return {e.registration_id: e for e in parse_registry(PAGE)}


def test_parse_programs_dates_and_list_cells():
    entries = _entries()
    assert len(entries) == 9
    hib = entries["DC481494"]
    assert (hib.program, hib.effective_date) == ("qualifying", date(2024, 2, 20))
    operators = [(p.name, p.registration_number) for p in hib.parties if p.role == "operator"]
    assert operators == [
        ("Core Scientific, Inc.", "DC481494-OP1"),
        ("Coreweave Compute Acquisition Co. II. LLC", "DC481494-OP2"),
        ("CCAC IV.II, LLC", "DC481494-OP3"),
    ]
    assert not [p for p in hib.parties if p.role == "owner"]  # blank on the page
    assert entries["LD565448"].program == "large"


def test_row_with_a_mistyped_party_id_is_keyed_by_owner():
    cedarvale = _entries()["DC961705"]
    assert cedarvale.other_ids == ["DC737649"]


def test_changed_columns_raise():
    with pytest.raises(RegistryFormatError):
        parse_registry(PAGE.replace("Occupant Name", "Tenant Name"))


def test_name_helpers():
    assert entity_key("Abilene Dc 1, LLC") == entity_key("Abilene DC 1 LLC") == "abilene dc 1"
    assert name_tokens("Microsoft Corporation (SAT 89-90)") == name_tokens(
        "Microsoft Sat 8990 Data Center"
    )
    assert stem_tokens("Lancium Abilene Clean Campus III") == stem_tokens(
        "Lancium Abilene Clean Campus"
    )


def test_company_lookup_exact_vs_brand():
    assert find_company("Oracle America Cloud Services, LLC")[0].ticker == "ORCL"
    assert find_company("Oracle America Cloud Services, LLC")[1] is True
    company, exact = find_company("Vantage Data Centers TX11, LLC")
    assert (company.name, company.ticker, exact) == ("Vantage Data Centers", None, False)
    assert find_company("Metal Works LLC") is None


def _tceq_site(session, name, applicant):
    site = Site(name=name, county="Taylor", aliases=[applicant])
    sig = Signal(site=site, type=SignalType.AIR_PERMIT, source="tceq_air", permit_number="1")
    session.add_all([site, sig])
    session.add(
        Extraction(
            signal=sig, extractor="t", extractor_version="0", fields={"applicant": applicant}
        )
    )
    session.flush()
    return site


def _snapshot(session):
    n = session.scalar(select(func.count()).select_from(Document))
    doc = Document(
        source="comptroller_dc_registry", sha256=f"{n:064d}", local_path="p", size_bytes=1
    )
    session.add(doc)
    session.flush()
    return doc


def test_import_matches_by_entity_then_phase_and_links_tickers(session):
    longhorn = _tceq_site(session, "Longhorn Data Center", "Abilene Dc 1, LLC")
    _tceq_site(session, "Microsoft Sat 8990 Data Center", "Microsoft Corporation")
    _tceq_site(session, "Sentry Gateway Data Center", "Microsoft Corporation")

    report = import_registry(session, list(_entries().values()), _snapshot(session))
    how = {m.entry.registration_id: (m.how, m.site.name) for m in report.new}

    assert how["LD565448"] == ("entity", "Longhorn Data Center")  # owner Abilene DC 1, LLC
    assert how["LD090472"] == ("phase", "Longhorn Data Center")  # Campus III, shares Lancium
    # "Microsoft Corporation" is on two TCEQ sites, so the name decides, not the entity.
    assert how["LD772702"] == ("name", "Microsoft Sat 8990 Data Center")
    # Childress shares Lancium but is not a phase of Abilene: its own site.
    assert how["LD053115"][0] == "new"
    # Same project in both programs ends up on one site.
    assert how["LD449964"] == ("name", how["DC961705"][1])

    # The registry names Oracle directly, but the entry is tied to Longhorn by a name match,
    # so Oracle's link to Longhorn is only medium.
    companies = site_companies(session, longhorn)
    assert ("occupies", "Oracle", "ORCL", "medium") in companies
    assert ("operates", "Lancium", None, "medium") in companies


def test_second_pull_reports_unchanged_and_changes(session):
    entries = list(_entries().values())
    import_registry(session, entries, _snapshot(session))
    signals = session.scalar(select(func.count()).select_from(Signal))
    links = session.scalar(select(func.count()).select_from(Link))

    again = import_registry(session, entries, _snapshot(session))
    assert (len(again.new), again.unchanged) == (0, 9)
    assert session.scalar(select(func.count()).select_from(Link)) == links

    entries[0].end_date = date(2030, 1, 1)
    changed = import_registry(session, entries[1:], _snapshot(session))
    assert [e.registration_id for e in changed.changed] == []  # entry 0 not in this pull
    assert [s.permit_number for s in changed.dropped] == [entries[0].registration_id]
    assert session.scalar(select(func.count()).select_from(Signal)) == signals


def test_party_links_on_registry_only_sites_are_high(session):
    report = import_registry(session, list(_entries().values()), _snapshot(session))
    alamo = next(m.site for m in report.new if m.entry.registration_id == "LD543087")
    assert ("operates", "Alphabet (Google)", "GOOGL", "high") in site_companies(session, alamo)
    owns = session.scalars(
        select(Link).where(Link.link_type == LinkType.OWNS, Link.to_id == alamo.id)
    ).all()
    assert owns and all(link.confidence == Confidence.HIGH and link.evidence_url for link in owns)
