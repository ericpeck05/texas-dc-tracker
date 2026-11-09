"""Permit holders as entities: one entity per legal name, company links, people kept out."""

from sqlalchemy import func, select

from tracker.db.models import Entity, Link, LinkType, RecordType
from tracker.entities import EntityBook, is_organization, site_companies, site_entities
from tracker.ingest import ingest_tceq_air


def _filing(applicant: str, rn: str = "RN112345678", permit: str = "176543") -> list[str]:
    return [
        f"Applicant: {applicant}\n"
        f"Regulated Entity Number: {rn}\n"
        f"Air Quality Permit Number {permit}\n"
        "The site is located in Bexar County, Texas.",
        "The applicant proposes ten (10) 3,000 kW diesel-fired emergency generators.",
    ]


def _ingest(session, pdf_factory, tmp_path, pages, name):
    pdf = pdf_factory(pages, name)
    return ingest_tceq_air(session, pdf.read_bytes(), raw_dir=tmp_path, filename=name)


def test_applicant_named_exactly_links_company_high(session, pdf_factory, tmp_path):
    result = _ingest(session, pdf_factory, tmp_path, _filing("Microsoft Corporation"), "a.pdf")
    assert site_entities(session, result.site) == [("filed_by", "Microsoft Corporation", "high")]
    assert site_companies(session, result.site) == [("filed_by", "Microsoft", "MSFT", "high")]


def test_brand_in_project_llc_is_medium(session, pdf_factory, tmp_path):
    result = _ingest(
        session, pdf_factory, tmp_path, _filing("Vantage Data Centers Tx11, LLC"), "a.pdf"
    )
    assert site_companies(session, result.site) == [
        ("filed_by", "Vantage Data Centers", None, "medium")
    ]


def test_same_entity_across_spellings_and_filings(session, pdf_factory, tmp_path):
    _ingest(session, pdf_factory, tmp_path, _filing("Abilene Dc 1, LLC"), "a.pdf")
    _ingest(session, pdf_factory, tmp_path, _filing("Abilene DC 1 LLC", permit="176544"), "b.pdf")
    assert session.scalar(select(func.count()).select_from(Entity)) == 1
    links = session.scalars(select(Link).where(Link.from_type == RecordType.ENTITY)).all()
    assert len(links) == 1 and links[0].link_type == LinkType.FILED_BY


def test_name_cut_short_joins_the_full_name(session):
    book = EntityBook(session)
    short = book.get("Abilene Data Center Campus Master")
    full = book.get("Abilene Data Center Campus Master Association")
    assert short is full
    assert full.legal_name == "Abilene Data Center Campus Master Association"
    # And the other way round: the short name seen after the full one.
    assert EntityBook(session).get("Abilene Data Center Campus Master") is full


def test_people_never_become_entities(session, pdf_factory, tmp_path):
    assert not is_organization("Jane Q. Smith")
    assert is_organization("Bank of America, National Association")
    result = _ingest(session, pdf_factory, tmp_path, _filing("Jane Q. Smith"), "a.pdf")
    assert result.created
    assert session.scalar(select(func.count()).select_from(Entity)) == 0
