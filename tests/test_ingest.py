from sqlalchemy import func, select

from tracker.db.models import Document, Equipment, Site, SiteStatus
from tracker.ingest import ingest_tceq_air

PAGES = [
    "Applicant: Example Data Campus LLC\n"
    "Regulated Entity Number: RN112345678\n"
    "Air Quality Permit Number 176543\n"
    "The site is located in Hunt County, Texas.",
    "The applicant proposes forty (40) Caterpillar 3516C diesel-fired emergency generators, "
    "each rated at 2,500 kW, and two (2) 30,000-gallon diesel storage tanks.",
]


def test_pdf_to_equipment_rows(session, pdf_factory, tmp_path):
    pdf = pdf_factory(PAGES)
    result = ingest_tceq_air(session, pdf.read_bytes(), raw_dir=tmp_path / "raw", filename=pdf.name)

    assert result.created
    assert result.site.name == "Example Data Campus LLC"
    assert result.site.county == "Hunt"
    assert result.site.status == SiteStatus.FILED
    assert result.signal.permit_number == "176543"

    eq = session.scalars(select(Equipment).order_by(Equipment.id)).all()
    gens, tanks = eq
    assert (gens.count, gens.unit_size, gens.unit, gens.manufacturer, gens.model, gens.page) == (
        40,
        2500,
        "kW",
        "Caterpillar",
        "3516C",
        2,
    )
    assert (tanks.count, tanks.unit_size, tanks.unit) == (2, 30000, "gal")
    assert all(e.site_id == result.site.id for e in eq)
    assert (tmp_path / "raw" / "tceq_air" / f"{result.document.sha256}.pdf").exists()


def test_reingest_is_skipped_and_rn_reuses_site(session, pdf_factory, tmp_path):
    first = pdf_factory(PAGES, "a.pdf")
    ingest_tceq_air(session, first.read_bytes(), raw_dir=tmp_path, filename="a.pdf")
    again = ingest_tceq_air(session, first.read_bytes(), raw_dir=tmp_path, filename="a.pdf")
    assert not again.created

    amendment = pdf_factory(
        [PAGES[0], "Amendment adds four (4) 2,500 kW diesel generators."], "b.pdf"
    )
    ingest_tceq_air(session, amendment.read_bytes(), raw_dir=tmp_path, filename="b.pdf")

    assert session.scalar(select(func.count()).select_from(Site)) == 1
    assert session.scalar(select(func.count()).select_from(Document)) == 2
