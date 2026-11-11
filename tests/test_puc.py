"""Texas PUC dockets: filters, privacy, and matching to sites."""

from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from tracker.collectors.puc.interchange import (
    Docket,
    Snapshot,
    data_center_reason,
    is_project,
    parse_filings,
    read_xlsx,
)
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
from tracker.puc_import import applicants, county_of, import_puc


def _xlsx(rows: list[list[str]]) -> bytes:
    """A minimal workbook with inline strings, like the PUC export."""

    def cell(v):
        return f'<c t="inlineStr"><is><t>{v}</t></is></c>'

    body = "".join(f"<row>{''.join(cell(v) for v in r)}</row>" for r in rows)
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_read_export_and_drop_people():
    content = _xlsx(
        [
            ["Control #", "Item #", "File Stamp", "Filing Party", "Filing Description"],
            ["60431", "1", "46321", "SKYBOX DATACENTERS, LLC", "Complaint regarding BZ-465"],
            ["60431", "2", "46322", "JANE Q PUBLIC", "Comment about my power bill"],
        ]
    )
    filings = parse_filings(read_xlsx(content))
    assert filings[0] == {
        "item": "1",
        "date": "2026-10-26",
        "party": "SKYBOX DATACENTERS, LLC",
        "description": "Complaint regarding BZ-465",
    }
    assert filings[1]["party"] is None and filings[1]["description"] is None


def test_project_and_data_center_filters():
    assert not is_project("PUC RULES & PROJECTS", "RULEMAKING TO IMPLEMENT LARGE LOAD STANDARDS")
    assert not is_project(
        "TEXAS-NEW MEXICO POWER COMPANY", "APPLICATION FOR AUTHORITY TO CHANGE RATES"
    )
    assert is_project("ACS DC HICKS FIELD ROAD, LLC", "COMPLAINT OF ACS DC HICKS FIELD ROAD, LLC")
    assert data_center_reason("ACS DC HICKS FIELD ROAD, LLC", "COMPLAINT OF ACS ...", [])
    assert (
        data_center_reason(
            "LANCIUM QSE I, LLC", "APPLICATION FOR A RETAIL ELECTRIC PROVIDER CERTIFICATE", []
        )
        is None
    )
    assert data_center_reason(
        "EL PASO ELECTRIC COMPANY",
        "RATE RIDER FOR A NEW DATA CENTER TO BE LOCATED IN EL PASO TEXAS",
        [],
    )
    assert (
        data_center_reason("MESQUITE H2, LLC", "COMPLAINT", [{"description": "hydrogen"}]) is None
    )


def test_applicants_and_county():
    d = Docket(
        "59220",
        "FGE GOODNIGHT I, LLC AND CRUSOE ENERGY SYSTEMS LLC",
        "APPLICATION OF CRUSOE ENERGY SYSTEMS LLC, FGE GOODNIGHT I, LLC AND ENSIGN "
        "INFRASTRUCTURE LLC FOR APPROVAL OF A NET METERING ARRANGEMENT",
    )
    names = applicants(d)
    assert "FGE GOODNIGHT I, LLC" in names and "CRUSOE ENERGY SYSTEMS LLC" in names
    assert "ENSIGN INFRASTRUCTURE LLC" in names
    assert county_of("NEW DATA CENTER TO BE LOCATED IN EL PASO TEXAS") == "El Paso"
    assert county_of("LINE IN BELL, BURNET, AND MILAM COUNTIES") is None


def _docket(control, party, description, when="46321"):
    return Docket(
        control,
        party,
        description,
        parse_filings(
            read_xlsx(
                _xlsx(
                    [
                        ["Control #", "Item #", "File Stamp", "Filing Party", "Filing Description"],
                        [control, "1", when, party.split(" AND ")[0], description[:40]],
                    ]
                )
            )
        ),
        "test",
    )


def test_import_matches_creates_and_reviews(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        book = EntityBook(session)
        goodnight = Site(name="Goodnight Data Center Buildings 1-4", county="Carson")
        a = Site(name="Skybox Plano")
        b = Site(name="Skybox Houston")
        for s in (goodnight, a, b):
            s.set_status(SiteStatus.FILED)
        session.add_all([goodnight, a, b])
        session.flush()
        for s in (a, b):
            link_party(
                session,
                book,
                "Skybox Datacenters, LLC",
                s,
                LinkType.OWNS,
                Confidence.HIGH,
                evidence_url="https://comptroller.example",
                note="owner",
            )
        doc = Document(
            source="puc_interchange", url="u", sha256="p" * 64, local_path="x", size_bytes=1
        )
        session.add(doc)
        session.flush()
        dockets = [
            _docket(
                "59220",
                "FGE GOODNIGHT I, LLC AND CRUSOE ENERGY SYSTEMS LLC",
                "APPLICATION OF CRUSOE ENERGY SYSTEMS LLC, FGE GOODNIGHT I, LLC FOR APPROVAL",
            ),
            _docket(
                "60431",
                "SKYBOX DATACENTERS, LLC",
                "COMPLAINT OF SKYBOX DATACENTERS, LLC AGAINST ERCOT",
            ),
            _docket(
                "60425",
                "ACS DC HICKS FIELD ROAD, LLC",
                "COMPLAINT OF ACS DC HICKS FIELD ROAD, LLC AGAINST ERCOT",
            ),
            _docket(
                "56903",
                "EL PASO ELECTRIC COMPANY",
                "APPLICATION OF EL PASO ELECTRIC COMPANY FOR A RATE RIDER FOR A NEW DATA CENTER "
                "TO BE LOCATED IN EL PASO TEXAS",
            ),
            _docket(
                "59076",
                "EL PASO ELECTRIC COMPANY",
                "APPLICATION OF EL PASO ELECTRIC COMPANY TO AMEND ITS CERTIFICATE FOR A PLANT",
            ),
        ]
        report = import_puc(session, dockets, doc)
        got = {m.docket.control: (m.how, m.site.name if m.site else None) for m in report.matched}
        assert got["59220"] == ("name", "Goodnight Data Center Buildings 1-4")
        assert got["60431"] == ("review", None)
        assert got["60425"] == ("new", "ACS DC Hicks Field Road")
        assert got["56903"] == (
            "new",
            "El Paso Electric Company data center customer (El Paso County)",
        )
        assert got["59076"] == (
            "utility",
            "El Paso Electric Company data center customer (El Paso County)",
        )
        assert len(report.review) == 2
        sig = session.scalar(select(Signal).where(Signal.permit_number == "59220"))
        assert sig.type == SignalType.GRID and str(sig.filing_date) == "2026-10-26"
        dev = session.scalars(
            select(Link).where(Link.link_type == LinkType.DEVELOPS, Link.to_id == goodnight.id)
        ).all()
        assert dev and all(link.confidence == Confidence.MEDIUM for link in dev)
        assert all(link.from_type == RecordType.ENTITY for link in dev)

        # Again: nothing new. A new filing on a docket is a change, not a new signal.
        again = import_puc(session, dockets, doc)
        assert again.new_signals == 0 and again.changed == 0
        dockets[0].filings.append(
            {"item": "2", "date": "2026-11-01", "party": None, "description": None}
        )
        assert import_puc(session, dockets, doc).changed == 1

    snap = Snapshot(["u"], [d.as_dict() for d in dockets])
    assert Snapshot.from_bytes(snap.to_bytes()).dockets == snap.dockets
