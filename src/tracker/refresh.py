"""Weekly refresh: run every collector, report what changed, and flag broken sources.

Steps, in order (a failing step is recorded and the rest still run):

1. TCEQ Records Online discovery: new permits and documents.
2. Comptroller data center registry: new, changed, and dropped entries.
3. Comptroller Chapter 312 tax abatements: data center agreements.
4. TCEQ Central Registry: RNs, counties, stormwater and air authorizations.
5. Franchise tax lookups for entities not looked up yet.

What changed is found by id watermarks taken before the run: every site, signal, status
change, and company link with a higher id is new. Health checks compare each source with
the last run, so a scraper that quietly returns nothing (a portal redesign) is flagged
instead of looking like a quiet week.

Each run writes data/refresh/<timestamp>.json and .md, and copies the report to
data/refresh/latest.md.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tracker import jobs
from tracker.config import Settings
from tracker.db.models import (
    Company,
    Link,
    RecordType,
    Signal,
    Site,
    SiteStatusChange,
)
from tracker.db.session import session_scope
from tracker.entities import RANK

Echo = Callable[[str], None]
DROP_WARN = 0.5  # a source returning under half of last run's count is suspicious


@dataclass
class Check:
    level: str  # ok, warn, fail
    source: str
    message: str


@dataclass
class StepResult:
    name: str
    ok: bool
    seconds: float
    counts: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class RefreshReport:
    started: str
    finished: str = ""
    steps: list[StepResult] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    new_sites: list[dict] = field(default_factory=list)
    new_signals: list[dict] = field(default_factory=list)
    status_changes: list[dict] = field(default_factory=list)
    new_company_ties: list[dict] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(c.level == "fail" for c in self.checks)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, default=str)


def _max_id(session: Session, model) -> int:
    return session.scalar(select(func.max(model.id))) or 0


def watermarks(session: Session) -> dict[str, int]:
    return {
        "site": _max_id(session, Site),
        "signal": _max_id(session, Signal),
        "status": _max_id(session, SiteStatusChange),
        "link": _max_id(session, Link),
    }


def changes_since(session: Session, marks: dict[str, int], report: RefreshReport) -> None:
    for site in session.scalars(select(Site).where(Site.id > marks["site"]).order_by(Site.id)):
        report.new_sites.append({"id": site.id, "name": site.name, "county": site.county})
    for sig in session.scalars(
        select(Signal).where(Signal.id > marks["signal"]).order_by(Signal.id)
    ):
        url = sig.document.url if sig.document else None
        if url is None:
            url = next((ex.fields.get("url") for ex in sig.extractions), None)
        report.new_signals.append(
            {
                "site": sig.site.name if sig.site else None,
                "type": str(sig.type),
                "permit": sig.permit_number,
                "date": sig.filing_date.isoformat() if sig.filing_date else None,
                "equipment_rows": len(sig.equipment),
                "url": url,
            }
        )
    for ch in session.scalars(
        select(SiteStatusChange)
        # New sites get a first status when created; that is not a change.
        .where(SiteStatusChange.id > marks["status"], SiteStatusChange.site_id <= marks["site"])
        .order_by(SiteStatusChange.id)
    ):
        report.status_changes.append(
            {
                "site": ch.site.name,
                "from": str(ch.old_status) if ch.old_status else None,
                "to": str(ch.new_status),
            }
        )
    # A company newly tied to a site: a new entity->site link whose entity has a company,
    # or a new company->entity link whose entity is on a site.
    ties: dict[tuple[str, str], str] = {}
    new_links = session.scalars(select(Link).where(Link.id > marks["link"])).all()
    for link in new_links:
        if link.from_type == RecordType.ENTITY and link.to_type == RecordType.SITE:
            pairs = [(link, cl) for cl in _company_links(session, link.from_id)]
        elif link.from_type == RecordType.COMPANY and link.to_type == RecordType.ENTITY:
            pairs = [(el, link) for el in _site_links(session, link.to_id)]
        else:
            continue
        for el, cl in pairs:
            conf = min(el.confidence, cl.confidence, key=RANK.get)
            site = session.get(Site, el.to_id)
            company = session.get(Company, cl.from_id)
            key = (site.name, company.name)
            if key not in ties or RANK[conf] > RANK[ties[key]]:
                ties[key] = conf
    for (site, company), conf in sorted(ties.items()):
        report.new_company_ties.append({"site": site, "company": company, "confidence": str(conf)})


def _company_links(session: Session, entity_id: int) -> list[Link]:
    return session.scalars(
        select(Link).where(
            Link.from_type == RecordType.COMPANY,
            Link.to_type == RecordType.ENTITY,
            Link.to_id == entity_id,
        )
    ).all()


def _site_links(session: Session, entity_id: int) -> list[Link]:
    return session.scalars(
        select(Link).where(
            Link.from_type == RecordType.ENTITY,
            Link.from_id == entity_id,
            Link.to_type == RecordType.SITE,
        )
    ).all()


def health_checks(report: RefreshReport, previous: dict | None) -> list[Check]:
    checks = []
    prev_counts = {s["name"]: s["counts"] for s in (previous or {}).get("steps", [])}
    for step in report.steps:
        if not step.ok:
            checks.append(Check("fail", step.name, f"step failed: {step.error}"))
            continue
        c, before = step.counts, prev_counts.get(step.name, {})
        if step.name == "tceq_discovery":
            if c["permits"] == 0:
                checks.append(Check("fail", step.name, "search returned no permits"))
            elif before.get("permits") and c["permits"] < DROP_WARN * before["permits"]:
                msg = f"{c['permits']} permits, {before['permits']} last run"
                checks.append(Check("warn", step.name, msg))
            if c["failed"]:
                checks.append(Check("warn", step.name, f"{c['failed']} documents failed"))
        elif step.name == "comptroller_registry":
            if c["entries"] == 0:
                checks.append(Check("fail", step.name, "registry page had no entries"))
            elif before.get("entries") and c["entries"] < DROP_WARN * before["entries"]:
                msg = f"{c['entries']} entries, {before['entries']} last run"
                checks.append(Check("warn", step.name, msg))
            if c["dropped"] > max(5, 0.1 * c["entries"]):
                checks.append(Check("warn", step.name, f"{c['dropped']} entries no longer listed"))
        elif step.name == "comptroller_abatements":
            if c["agreements"] == 0:
                checks.append(Check("fail", step.name, "abatement file had no agreements"))
            elif before.get("agreements") and c["agreements"] < DROP_WARN * before["agreements"]:
                msg = f"{c['agreements']} agreements, {before['agreements']} last run"
                checks.append(Check("warn", step.name, msg))
            if before.get("data_centers") and c["data_centers"] < before["data_centers"]:
                msg = f"{c['data_centers']} data center agreements, was {before['data_centers']}"
                checks.append(Check("warn", step.name, msg))
        elif step.name == "tceq_central_registry":
            if c["data_centers"] == 0:
                checks.append(Check("fail", step.name, "no data center sites returned"))
            elif (
                before.get("data_centers")
                and c["data_centers"] < DROP_WARN * before["data_centers"]
            ):
                msg = f"{c['data_centers']} data center sites, {before['data_centers']} last run"
                checks.append(Check("warn", step.name, msg))
        elif step.name == "puc_dockets":
            if c["dockets"] == 0:
                checks.append(Check("fail", step.name, "no data center dockets returned"))
            elif before.get("dockets") and c["dockets"] < DROP_WARN * before["dockets"]:
                msg = f"{c['dockets']} dockets, {before['dockets']} last run"
                checks.append(Check("warn", step.name, msg))
        elif step.name == "owner_lookups":
            if c["names"] and c["failed"] >= c["names"]:
                checks.append(Check("fail", step.name, "every franchise tax lookup failed"))
            elif c["failed"]:
                checks.append(Check("warn", step.name, f"{c['failed']} lookups failed"))
    if not checks:
        checks.append(Check("ok", "all", "every source returned data"))
    return checks


def _run_step(report: RefreshReport, name: str, echo: Echo, fn: Callable[[], dict]) -> None:
    echo(f"== {name}")
    start = datetime.now(UTC)
    try:
        counts = fn()
        ok, error = True, None
    except Exception as exc:  # one broken source must not stop the others
        counts, ok, error = {}, False, f"{type(exc).__name__}: {exc}"
        echo(traceback.format_exc())
    seconds = round((datetime.now(UTC) - start).total_seconds(), 1)
    report.steps.append(StepResult(name, ok, seconds, counts, error))


def run_refresh(
    settings: Settings,
    engine: Engine,
    echo: Echo,
    *,
    steps: tuple[str, ...] = (
        "tceq_discovery",
        "comptroller_registry",
        "comptroller_abatements",
        "tceq_central_registry",
        "puc_dockets",
        "owner_lookups",
    ),
    out_dir: Path | None = None,
    collectors: dict[str, Callable[[], dict]] | None = None,
) -> tuple[RefreshReport, Path]:
    out_dir = out_dir or settings.data_dir / "refresh"
    out_dir.mkdir(parents=True, exist_ok=True)
    previous = _last_report(out_dir)
    report = RefreshReport(started=datetime.now(UTC).isoformat(timespec="seconds"))
    with session_scope(engine) as session:
        marks = watermarks(session)

    default = {
        "tceq_discovery": lambda: _discovery_counts(jobs.discover_tceq(settings, engine, echo)),
        "comptroller_registry": lambda: _registry_counts(
            jobs.pull_registry(settings, engine, echo)
        ),
        "comptroller_abatements": lambda: _abatement_counts(
            jobs.pull_abatements(settings, engine, echo)
        ),
        "tceq_central_registry": lambda: _central_registry_counts(
            jobs.pull_central_registry(settings, engine, echo)
        ),
        "puc_dockets": lambda: _puc_counts(jobs.pull_puc(settings, engine, echo)),
        "owner_lookups": lambda: _owner_counts(
            jobs.lookup_owners(settings, engine, echo, max_names=200)
        ),
    }
    collectors = {**default, **(collectors or {})}
    for name in steps:
        _run_step(report, name, echo, collectors[name])

    with session_scope(engine) as session:
        changes_since(session, marks, report)
    report.checks = health_checks(report, previous)
    report.finished = datetime.now(UTC).isoformat(timespec="seconds")

    stamp = report.started.replace(":", "").replace("-", "")[:15]
    (out_dir / f"{stamp}.json").write_text(report.to_json())
    md = render_markdown(report)
    path = out_dir / f"{stamp}.md"
    path.write_text(md)
    (out_dir / "latest.md").write_text(md)
    return report, path


def _discovery_counts(r: jobs.DiscoveryResult) -> dict:
    return {
        "permits": r.permits,
        "documents": r.documents,
        "fetched": r.fetched,
        "already": r.already,
        "failed": len(r.failed),
    }


def _registry_counts(r: jobs.RegistryResult) -> dict:
    return {
        "entries": r.entries,
        "new": len(r.report.new),
        "changed": len(r.report.changed),
        "dropped": len(r.report.dropped),
        "review": len(r.report.review),
    }


def _abatement_counts(r) -> dict:
    return {
        "agreements": r.agreements,
        "data_centers": r.data_centers,
        "new": len(r.new),
        "changed": len(r.changed),
        "review": len(r.review),
    }


def _central_registry_counts(r) -> dict:
    return {
        "sites": r.entities,
        "data_centers": r.data_centers,
        "placed": len(r.matched),
        "new_signals": r.new_signals,
        "changed": r.changed,
        "review": len(r.review),
    }


def _puc_counts(r) -> dict:
    return {
        "dockets": r.dockets,
        "new_signals": r.new_signals,
        "changed": r.changed,
        "review": len(r.review),
    }


def _owner_counts(r: jobs.OwnersResult) -> dict:
    return {
        "names": r.names,
        "found": r.found,
        "addresses": r.addresses,
        "failed": len(r.failed),
    }


def _last_report(out_dir: Path) -> dict | None:
    runs = sorted(out_dir.glob("*.json"))
    return json.loads(runs[-1].read_text()) if runs else None


def render_markdown(r: RefreshReport) -> str:
    lines = [f"# Refresh {r.started}", ""]
    status = "FAILED" if r.failed else "ok"
    lines.append(f"Status: **{status}** (finished {r.finished})")
    lines.append("")
    lines.append("## Health")
    for c in r.checks:
        lines.append(f"- {c.level.upper()} {c.source}: {c.message}")
    lines.append("")
    lines.append("## Sources")
    for s in r.steps:
        counts = ", ".join(f"{k} {v}" for k, v in s.counts.items()) or "-"
        lines.append(f"- {s.name} ({s.seconds}s): {'ok' if s.ok else 'FAILED'}; {counts}")
    lines.append("")
    lines.append(f"## New filings ({len(r.new_signals)})")
    for sig in r.new_signals:
        where = sig["site"] or "unmatched"
        word = {"incentive": "registration", "approval": "agreement", "stormwater": "permit"}.get(
            sig["type"], "permit"
        )
        permit = f" {word} {sig['permit']}" if sig["permit"] else ""
        eq = f", {sig['equipment_rows']} equipment rows" if sig["equipment_rows"] else ""
        link = f" [document]({sig['url']})" if sig["url"] else ""
        lines.append(f"- {sig['date'] or 'undated'} {sig['type']}{permit}: {where}{eq}{link}")
    lines.append("")
    lines.append(f"## New sites ({len(r.new_sites)})")
    for site in r.new_sites:
        lines.append(f"- {site['name']} ({site['county'] or 'county unknown'})")
    if r.status_changes:
        lines.append("")
        lines.append("## Status changes")
        for ch in r.status_changes:
            lines.append(f"- {ch['site']}: {ch['from'] or 'none'} -> {ch['to']}")
    lines.append("")
    lines.append(f"## New company ties ({len(r.new_company_ties)})")
    for t in r.new_company_ties:
        lines.append(f"- {t['site']}: {t['company']} [{t['confidence']}]")
    lines.append("")
    lines.append("Not investment advice.")
    return "\n".join(lines) + "\n"
