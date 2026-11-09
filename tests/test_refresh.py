"""Weekly refresh: change report, health checks, and the launchd job definition."""

from __future__ import annotations

import plistlib
from pathlib import Path

from tracker.config import Settings
from tracker.db.models import Confidence, LinkType, Signal, SignalType, Site, SiteStatus
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import EntityBook, link_party
from tracker.refresh import run_refresh
from tracker.schedule import build_plist, protected_folder

GOOD = {
    "tceq_discovery": {"permits": 40, "documents": 60, "fetched": 0, "already": 60, "failed": 0},
    "comptroller_registry": {"entries": 177, "new": 0, "changed": 0, "dropped": 0, "review": 0},
    "comptroller_abatements": {
        "agreements": 1391,
        "data_centers": 16,
        "new": 0,
        "changed": 0,
        "review": 0,
    },
    "tceq_central_registry": {
        "sites": 346,
        "data_centers": 258,
        "placed": 0,
        "new_signals": 0,
        "changed": 0,
        "review": 0,
    },
    "owner_lookups": {"names": 0, "found": 0, "addresses": 0, "failed": 0},
}


def _setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    settings = Settings(data_dir=tmp_path)
    with session_scope(engine) as session:
        old = Site(name="Old Site")
        old.set_status(SiteStatus.FILED)
        session.add(old)
    return engine, settings


def _run(engine, settings, counts, extra=None):
    def collector(name):
        def run():
            if extra and name in extra:
                extra[name]()
            return counts[name]

        return run

    return run_refresh(
        settings,
        engine,
        lambda _line: None,
        collectors={name: collector(name) for name in counts},
    )


def test_report_lists_only_what_this_run_added(tmp_path):
    engine, settings = _setup(tmp_path)

    def add_filing():
        with session_scope(engine) as session:
            site = Site(name="New Campus", county="Ellis")
            session.add(site)
            session.add(
                Signal(
                    site=site, type=SignalType.AIR_PERMIT, source="tceq_air", permit_number="190001"
                )
            )
            session.flush()
            link_party(
                session,
                EntityBook(session),
                "Microsoft Corporation",
                site,
                LinkType.FILED_BY,
                Confidence.HIGH,
                evidence_url=None,
                note="t",
            )
            old = session.query(Site).filter_by(name="Old Site").one()
            old.set_status(SiteStatus.APPROVED, note="permit issued")

    report, path = _run(engine, settings, GOOD, {"tceq_discovery": add_filing})
    assert [s["name"] for s in report.new_sites] == ["New Campus"]
    assert [(s["site"], s["permit"]) for s in report.new_signals] == [("New Campus", "190001")]
    assert report.new_company_ties == [
        {"site": "New Campus", "company": "Microsoft", "confidence": "high"}
    ]
    assert report.status_changes == [{"site": "Old Site", "from": "filed", "to": "approved"}]
    assert [c.level for c in report.checks] == ["ok"]
    text = (tmp_path / "refresh" / "latest.md").read_text()
    assert "New Campus" in text and path.exists()

    # A second run with nothing new reports nothing new.
    again, _ = _run(engine, settings, GOOD)
    assert not (again.new_sites or again.new_signals or again.new_company_ties)


def test_health_checks_flag_empty_and_shrinking_sources(tmp_path):
    engine, settings = _setup(tmp_path)
    _run(engine, settings, GOOD)
    bad = {
        "tceq_discovery": {**GOOD["tceq_discovery"], "permits": 0},
        "comptroller_registry": {**GOOD["comptroller_registry"], "entries": 60},
        "comptroller_abatements": {**GOOD["comptroller_abatements"], "agreements": 0},
        "tceq_central_registry": {**GOOD["tceq_central_registry"], "data_centers": 0},
        "owner_lookups": {"names": 5, "found": 0, "addresses": 0, "failed": 5},
    }
    report, _ = _run(engine, settings, bad)
    levels = {(c.source, c.level) for c in report.checks}
    assert ("tceq_discovery", "fail") in levels
    assert ("comptroller_registry", "warn") in levels
    assert ("comptroller_abatements", "fail") in levels
    assert ("tceq_central_registry", "fail") in levels
    assert ("owner_lookups", "fail") in levels
    assert report.failed


def test_a_crashing_step_does_not_stop_the_others(tmp_path):
    engine, settings = _setup(tmp_path)

    def boom():
        raise RuntimeError("portal redesigned")

    report, _ = run_refresh(
        settings,
        engine,
        lambda _line: None,
        collectors={
            "tceq_discovery": boom,
            "comptroller_registry": lambda: GOOD["comptroller_registry"],
            "comptroller_abatements": lambda: GOOD["comptroller_abatements"],
            "tceq_central_registry": lambda: GOOD["tceq_central_registry"],
            "owner_lookups": lambda: GOOD["owner_lookups"],
        },
    )
    assert [s.ok for s in report.steps] == [False, True, True, True, True]
    assert any(c.level == "fail" and "portal redesigned" in c.message for c in report.checks)


def test_launchd_plist():
    plist = plistlib.loads(build_plist("/opt/homebrew/bin/uv", Path("/p"), weekday=1, hour=6))
    assert plist["ProgramArguments"][-2:] == ["tracker", "refresh"]
    assert plist["StartCalendarInterval"] == {"Weekday": 1, "Hour": 6, "Minute": 0}
    assert plist["StandardOutPath"] == "/p/data/refresh/launchd.log"
    assert protected_folder(Path.home() / "Downloads" / "x") == "Downloads"
    assert protected_folder(Path("/opt/x")) is None
