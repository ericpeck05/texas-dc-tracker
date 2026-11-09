"""TCEQ Central Registry: parsing, data center filter, and site matching."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from tracker.central_registry_import import _known_company, import_central_registry
from tracker.collectors.tceq.central_registry import (
    Snapshot,
    data_center_reason,
    parse_rows,
    rn_urls,
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


def _row(rn, name, program, number, *, principal="Acme Data LLC", county="ELLIS", **kw):
    return {
        "ref_num_txt": rn,
        "reg_ent_name": name,
        "re_phys_loc_addr_county": county,
        "princ_name": principal.upper(),
        "princ_legal_name": principal,
        "name_txt": "CORPORATION",
        "program_code": program,
        "additional_id_text": number,
        "additional_id_status": "ACTIVE",
        "affil_begin_dt": "2026-03-01T00:00:00.000",
        "_dataset": "t34q-qzi3",
        **kw,
    }


def test_parse_groups_programs_and_drops_people_and_contractors():
    rows = [
        _row("RN100000001", "BIG DATA CENTER", "STORM", "TXR15AAAA"),
        _row("RN100000001", "BIG DATA CENTER", "AIRNSR", "177001"),
        _row("RN100000001", "BIG DATA CENTER", "AIRNSR", "177001"),  # same ID from two queries
        _row(
            "RN100000002",
            "SMITH, JOHN",
            "STORM",
            "TXR15BBBB",
            principal="Smith, John",
            name_txt="INDIVIDUAL OWNER TYPE",
        ),
        _row(
            "RN100000003",
            "SITE A DATA CENTER",
            "STORM",
            "TXR15CCCC",
            principal="Big Builders Construction, Inc.",
        ),
        _row(
            "RN100000004",
            "X",
            "STORM",
            "TXR15DDDD",
            affil_begin_dt="1800-01-01T00:00:00.000",
            status_dt="2025-05-05T00:00:00.000",
        ),
    ]
    ents = {e.rn: e for e in parse_rows(rows)}
    assert "RN100000002" not in ents
    big = ents["RN100000001"]
    assert big.name == "Big Data Center" and big.county == "Ellis"
    assert [(p.program, p.number) for p in big.programs] == [
        ("AIRNSR", "177001"),
        ("STORM", "TXR15AAAA"),
    ]
    assert big.programs[1].is_construction_stormwater
    assert ents["RN100000003"].principal is None
    assert ents["RN100000003"].contractor == "Big Builders Construction, Inc."
    assert ents["RN100000004"].programs[0].since == date(2025, 5, 5)
    snap = Snapshot(["u"], rows)
    assert Snapshot.from_bytes(snap.to_bytes()).rows == rows


def test_rn_urls_chunk_and_skip_junk():
    urls = rn_urls([f"RN1000000{i:02d}" for i in range(45)] + ["junk"])
    assert len(urls) == 2 * 5  # two chunks, five regional datasets
    assert "junk" not in "".join(urls)


def test_data_center_filter():
    def ent(name, industry=None, principal="Acme Data LLC", desc=None):
        row = _row(
            "RN1",
            name,
            "STORM",
            "TXR15A",
            principal=principal,
            indus_type_cd_name=industry,
            reg_ent_desc_txt=desc,
        )
        return parse_rows([row])[0]

    assert data_center_reason(ent("SAT 40 DATA CENTER"), _known_company)
    assert data_center_reason(ent("BLDG 2", desc="DATA CENTER BUILDING"), _known_company)
    hosting = "518210 - Data Processing, Hosting, and Related Services"
    assert data_center_reason(ent("PRINT SHOP", hosting), _known_company) is None
    assert data_center_reason(ent("AUS3", hosting, "CyrusOne LLC"), _known_company)
    assert data_center_reason(ent("CBP SWEETWATER 2 DATA CENTER"), _known_company) is None
    assert data_center_reason(ent("QTS DATA CENTER SUBSTATION PROJECT"), _known_company) is None


def _setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        book = EntityBook(session)
        known = [
            ("Longhorn Data Center", "Taylor", ["RN200000001"], None),
            ("Thelma Data Center", None, [], "Homebound Group LLC"),
            ("Journey Data Center", None, [], "Homebound Group LLC"),
            ("San Antonio Data Center II", "Bexar", [], "CyrusOne LLC"),
            ("Cyrusone Houston West III", "Harris", [], "CyrusOne LLC"),
        ]
        for name, county, rns, owner in known:
            site = Site(name=name, county=county, tceq_rns=rns)
            site.set_status(SiteStatus.FILED)
            session.add(site)
            session.flush()
            if owner:
                link_party(
                    session,
                    book,
                    owner,
                    site,
                    LinkType.OWNS,
                    Confidence.HIGH,
                    evidence_url="https://comptroller.example",
                    note="t",
                )
        sig = Signal(
            site=site, type=SignalType.AIR_PERMIT, source="tceq_air", permit_number="177999"
        )
        session.add(sig)
    return engine


def _import(engine, rows):
    with session_scope(engine) as session:
        doc = Document(
            source="tceq_central_registry",
            url="u",
            sha256=str(hash(str(rows))),
            local_path="x",
            size_bytes=1,
        )
        existing = session.scalar(select(Document).where(Document.sha256 == doc.sha256))
        if existing is None:
            session.add(doc)
            session.flush()
        report = import_central_registry(
            session, parse_rows(rows), existing or doc, today=date(2026, 11, 9)
        )
        return {m.entity.rn: (m.how, m.site.name) for m in report.matched}, report


def test_matching_and_signals(tmp_path):
    engine = _setup(tmp_path)
    rows = [
        # 1. Same RN as an air permit document.
        _row("RN200000001", "ABILENE CAMPUS", "STORM", "TXR15A001", county="TAYLOR"),
        # 2. Name beats owner: the principal owns two sites, the name picks one.
        _row(
            "RN200000002",
            "JOURNEY SITE",
            "AIRNSR",
            "177100",
            principal="Homebound Group LLC",
            county="HASKELL",
            reg_ent_desc_txt="DATA CENTER",
        ),
        # 3. Owner on several sites; the other one is in another county.
        _row(
            "RN200000003",
            "SAT5 SAT6",
            "AIRNSR",
            "177200",
            principal="CyrusOne LLC",
            county="BEXAR",
            indus_type_cd_name="518210 - Hosting",
        ),
        # 4. Owner on several sites, none in this county: own site, nothing to review.
        _row(
            "RN200000004",
            "DFW1",
            "STORM",
            "TXR15A004",
            principal="CyrusOne LLC",
            county="DENTON",
            indus_type_cd_name="518210 - Hosting",
            affil_begin_dt="2009-01-01T00:00:00.000",
        ),
        # 5. Air ID already known from a permit document: no duplicate air signal.
        _row("RN200000005", "NEW DATA CENTER", "AIRNSR", "177999", county="HILL"),
        _row("RN200000005", "NEW DATA CENTER", "STORM", "TXR15A005", county="HILL"),
        # Not a data center and not tracked: ignored.
        _row("RN200000006", "WAREHOUSE", "STORM", "TXR15A006"),
    ]
    got, report = _import(engine, rows)
    assert got["RN200000002"] == ("name", "Journey Data Center")
    assert got["RN200000003"] == ("owner", "San Antonio Data Center II")
    assert got["RN200000004"] == ("new", "Dfw1")
    assert got["RN200000005"] == ("new", "New Data Center")
    assert "RN200000001" not in got and "RN200000006" not in got

    with session_scope(engine) as session:
        longhorn = session.scalar(select(Site).where(Site.name == "Longhorn Data Center"))
        assert {s.type for s in longhorn.signals} == {SignalType.STORMWATER}
        assert longhorn.status == SiteStatus.UNDER_CONSTRUCTION  # recent TXR15, active
        dfw1 = session.scalar(select(Site).where(Site.name == "Dfw1"))
        assert dfw1.status == SiteStatus.FILED  # its construction permit is from 2009
        assert dfw1.tceq_rns == ["RN200000004"] and dfw1.county == "Denton"
        new = session.scalar(select(Site).where(Site.name == "New Data Center"))
        assert [s.permit_number for s in new.signals] == ["TXR15A005"]
        journey = session.scalar(select(Site).where(Site.name == "Journey Data Center"))
        assert journey.county == "Haskell" and "RN200000002" in journey.tceq_rns
        link = session.scalar(
            select(Link).where(
                Link.from_type == RecordType.SIGNAL,
                Link.to_id == journey.id,
                Link.link_type == LinkType.SAME_SITE,
            )
        )
        assert link.confidence == Confidence.MEDIUM

    # Again: nothing new. A status change is a new extraction, not a new signal.
    got, report = _import(engine, rows)
    assert got == {} and report.new_signals == 0 and report.changed == 0
    rows[0] = {**rows[0], "additional_id_status": "TERMINATED"}
    _, report = _import(engine, rows)
    assert report.changed == 1 and report.new_signals == 0


def test_principals_from_this_source_do_not_chain_sites(tmp_path):
    engine = _setup(tmp_path)
    rows = [
        _row(
            "RN300000001",
            "ALPHA DATA CENTER",
            "STORM",
            "TXR15B001",
            principal="Zeta Data LLC",
            county="WARD",
        ),
        _row(
            "RN300000002",
            "OMEGA DATA CENTER",
            "STORM",
            "TXR15B002",
            principal="Zeta Data LLC",
            county="WARD",
        ),
    ]
    got, _ = _import(engine, rows)
    assert got["RN300000002"] == ("new", "Omega Data Center")
