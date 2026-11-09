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


TITLE_V_PAGE = (
    "Statement of Basis of the Federal Operating Permit\n"
    "Example Campus Association\n"
    "Site Name: Example Data Center\n"
    "County: Taylor\n"
    "Permit Number: O9999\n"
)


def test_no_rn_creates_site_keyed_by_permit(session, pdf_factory, tmp_path):
    pdf = pdf_factory([TITLE_V_PAGE], "sob.pdf")
    result = ingest_tceq_air(session, pdf.read_bytes(), raw_dir=tmp_path, filename="sob.pdf")

    site = result.site
    assert (site.name, site.county, site.tceq_rns) == ("Example Data Center", "Taylor", [])
    assert site.aliases == ["Example Campus Association"]
    assert "permit O9999" in site.status_changes[0].note


def test_later_filing_with_same_permit_reuses_site_and_adds_rn(session, pdf_factory, tmp_path):
    first = pdf_factory([TITLE_V_PAGE], "a.pdf")
    ingest_tceq_air(session, first.read_bytes(), raw_dir=tmp_path, filename="a.pdf")
    second = pdf_factory([TITLE_V_PAGE + "Regulated Entity Number: RN111222333\n"], "b.pdf")
    result = ingest_tceq_air(session, second.read_bytes(), raw_dir=tmp_path, filename="b.pdf")

    assert session.scalar(select(func.count()).select_from(Site)) == 1
    assert result.site.tceq_rns == ["RN111222333"]


def test_no_rn_and_no_permit_leaves_site_unmatched(session, pdf_factory, tmp_path):
    pdf = pdf_factory(["Some unrelated text about four (4) diesel generators."], "x.pdf")
    result = ingest_tceq_air(session, pdf.read_bytes(), raw_dir=tmp_path, filename="x.pdf")
    assert result.site is None


def test_title_v_and_its_nsr_permit_share_a_site_in_either_order(session, pdf_factory, tmp_path):
    title_v = pdf_factory(
        [TITLE_V_PAGE + "Authorization No.: 177999 Issuance Date: 01/22/2025\n"], "tv.pdf"
    )
    nsr = pdf_factory(
        [
            "Technical Review\n"
            "Company Example DC 1, LLC Registration Number 177999\n"
            "County Taylor Regulated Entity Number RN112029079\n"
        ],
        "nsr.pdf",
    )
    # NSR first, then the Title V permit that names it.
    ingest_tceq_air(session, nsr.read_bytes(), raw_dir=tmp_path, filename="nsr.pdf")
    result = ingest_tceq_air(session, title_v.read_bytes(), raw_dir=tmp_path, filename="tv.pdf")
    assert session.scalar(select(func.count()).select_from(Site)) == 1
    assert result.site.tceq_rns == ["RN112029079"]
    assert "Example Data Center" in result.site.aliases


def test_nsr_filed_after_title_v_finds_site_by_reference(session, pdf_factory, tmp_path):
    title_v = pdf_factory(
        [TITLE_V_PAGE + "Authorization No.: 177999 Issuance Date: 01/22/2025\n"], "tv.pdf"
    )
    nsr = pdf_factory(["Technical Review\nRegistration Number 177999\n"], "nsr.pdf")
    ingest_tceq_air(session, title_v.read_bytes(), raw_dir=tmp_path, filename="tv.pdf")
    ingest_tceq_air(session, nsr.read_bytes(), raw_dir=tmp_path, filename="nsr.pdf")
    assert session.scalar(select(func.count()).select_from(Site)) == 1


def test_filing_that_names_a_same_site_permit_joins_that_site(session, pdf_factory, tmp_path):
    turbines = pdf_factory(
        ["Technical Review\nRegistration Number 177999\nRegulated Entity Number: RN112029079\n"],
        "egu.pdf",
    )
    generators = pdf_factory(
        [
            "Permit by Rule (PBR) Registration\nTechnical Review\n"
            "Registration No.: 177998 Regulated Entity No.: RN112029061\n"
            "The EGU Standard Permit No. 177999 is also located at this site.\n"
        ],
        "pbr.pdf",
    )
    ingest_tceq_air(session, turbines.read_bytes(), raw_dir=tmp_path, filename="egu.pdf")
    result = ingest_tceq_air(session, generators.read_bytes(), raw_dir=tmp_path, filename="pbr.pdf")
    assert session.scalar(select(func.count()).select_from(Site)) == 1
    assert result.site.tceq_rns == ["RN112029079", "RN112029061"]
