"""Hand-entered announced projects and site places, and compute sites filed by known companies."""

from __future__ import annotations

from datetime import date

import pytest

from tracker.announced import (
    SeedError,
    apply_places,
    apply_projects,
    read_places,
    read_projects,
)
from tracker.collectors.tceq.central_registry import RegulatedEntity, data_center_reason
from tracker.db.models import Signal, SignalType, Site
from tracker.db.session import init_db, make_engine, session_scope
from tracker.reference.companies import find_company
from tracker.views.sites import site_row

PROJECTS = (
    "site,county,city,company,role,status,date,title,source_url,note\n"
    "Starbase Compute,Cameron,,Space Exploration Technologies Corp.,owner,filed,2026-11-09,"
    "City hearing notice,https://example.test/notice,444 acres\n"
)
PLACES = "site,county,city,source_url,note\nGiga Texas Datacenter,Travis,Austin,https://x.test,\n"


def test_musk_companies_are_known():
    assert find_company("Tesla, Inc.")[0].ticker == "TSLA"
    assert find_company("Space Exploration Technologies Corp")[0].ticker == "SPCX"
    assert find_company("X.AI LLC")[0].name == "SpaceX"  # xAI merged into SpaceX
    assert find_company("Tesla Electric Contracting LLC") is None  # not every "Tesla"


def test_compute_site_of_a_known_company_counts_but_its_factory_does_not():
    def ent(name, desc):
        return RegulatedEntity(
            rn="RN1", name=name, description=desc, county="Cameron", city=None, address=None,
            industry="336414", principal="Space Exploration Technologies Corp", contractor=None,
            cn=None, status=None,
        )  # fmt: skip

    def known(name):
        found = find_company(name)
        return found[0].name if found else None

    assert data_center_reason(ent("Starbase Super-Compute Facility", None), known)
    assert data_center_reason(ent("Space Exploration Technologies", "rockets"), known) is None


def test_announced_project_and_place(tmp_path):
    (tmp_path / "p.csv").write_text(PROJECTS)
    (tmp_path / "pl.csv").write_text(PLACES)
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        session.add(Site(name="Giga Texas Datacenter"))
        session.flush()
        assert apply_projects(session, read_projects(tmp_path / "p.csv")) == 1
        assert apply_projects(session, read_projects(tmp_path / "p.csv")) == 0  # once only
        assert apply_places(session, read_places(tmp_path / "pl.csv")) == []
        site = session.query(Site).filter_by(name="Starbase Compute").one()
        (sig,) = session.query(Signal).filter_by(site_id=site.id).all()
        row = site_row(session, site)
        giga = session.query(Site).filter_by(name="Giga Texas Datacenter").one()

        assert (sig.type, sig.filing_date) == (SignalType.NOTICE, date(2026, 11, 9))
        assert row["county"] == "Cameron" and row["located"] == "county"
        assert row["companies"] == [{"company": "SpaceX", "ticker": "SPCX", "confidence": "medium"}]
        assert (giga.county, giga.city) == ("Travis", "Austin")


def test_rows_need_a_source(tmp_path):
    (tmp_path / "p.csv").write_text(PROJECTS.replace("https://example.test/notice", ""))
    with pytest.raises(SeedError):
        read_projects(tmp_path / "p.csv")
