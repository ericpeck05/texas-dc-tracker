"""Collection jobs shared by the CLI commands and the weekly refresh.

Each job takes an `echo` callback for progress lines and returns a small result with the
numbers the refresh report and health checks need.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tracker.abatement_import import AbatementReport, import_abatements
from tracker.central_registry_import import CRReport, import_central_registry
from tracker.collectors.comptroller.abatements import DETAIL_URL as ABATEMENT_URL
from tracker.collectors.comptroller.abatements import SOURCE as ABATEMENT_SOURCE
from tracker.collectors.comptroller.abatements import parse_detail
from tracker.collectors.comptroller.data_center_registry import (
    REGISTRY_URL,
    parse_registry,
)
from tracker.collectors.comptroller.data_center_registry import SOURCE as REGISTRY_SOURCE
from tracker.collectors.comptroller.franchise_tax import SOURCE as FRANCHISE_SOURCE
from tracker.collectors.comptroller.franchise_tax import (
    Lookup,
    address_query_url,
    name_query_url,
)
from tracker.collectors.tceq import central_registry as cr
from tracker.collectors.tceq.records_online import DEFAULT_KEYWORDS, RecordsOnline, plan_discovery
from tracker.config import PILOT_COUNTIES, Settings
from tracker.db.models import Document, Site
from tracker.db.session import session_scope
from tracker.entities import entity_key
from tracker.http import PoliteClient, RobotsDisallowed
from tracker.ingest import IngestResult, ingest_tceq_air
from tracker.owners import AGENT_OFFICE_MIN, apply_lookup, lookup_targets, saved_lookups
from tracker.registry_import import ImportReport, import_registry
from tracker.storage import save_raw

Echo = Callable[[str], None]


def report_line(result: IngestResult, label: str) -> str:
    if not result.created:
        return f"  skip  {label} (already ingested)"
    eq = result.signal.equipment
    site = result.site.name if result.site else "no RN, unmatched"
    county = result.site.county if result.site else None
    flag = "" if county is None or county in PILOT_COUNTIES else f"  [outside pilot: {county}]"
    return f"  ok    {label}: {len(eq)} equipment rows, site: {site}{flag}"


def backfill_site_name(document: Document, listed_name: str | None) -> None:
    """Record the search listing's site name and use it for a site the documents never named."""
    if not listed_name:
        return
    if document.listed_site_name is None:
        document.listed_site_name = listed_name
    for sig in document.signals:
        if sig.site is not None and sig.site.name.startswith("Unnamed site "):
            sig.site.aliases = [*sig.site.aliases, sig.site.name]
            sig.site.name = listed_name


# --- TCEQ discovery -------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    permits: int = 0
    documents: int = 0
    fetched: int = 0
    already: int = 0
    failed: list[str] = field(default_factory=list)


def discover_tceq(
    settings: Settings,
    engine: Engine,
    echo: Echo,
    *,
    keywords: tuple[str, ...] = DEFAULT_KEYWORDS,
    since: date = date(2022, 1, 1),
    max_permits: int = 0,
    dry_run: bool = False,
) -> DiscoveryResult:
    out = DiscoveryResult()
    with PoliteClient(settings) as client:
        ro = RecordsOnline(client)
        echo(f"Searching site names for {', '.join(keywords)} (since {since})")
        plan = plan_discovery(ro, keywords, since=since, max_permits=max_permits)
        for permit, name in plan.skipped_construction:
            echo(f"  skip  permit {permit} {name} (construction site, not a data center)")
        out.permits = len(plan.permits)
        out.documents = sum(len(p.documents) for p in plan.permits)
        echo(f"{out.permits} permits, {out.documents} documents to check")

        for p in plan.permits:
            echo(f"permit {p.permit_number}  {p.site_name or '?'}")
            if not p.documents:
                echo("  none  no technical review or final action listed")
            for doc in p.documents:
                label = f"{doc.title} {doc.begin_date or '?'} (content {doc.content_id})"
                with session_scope(engine) as session:
                    seen = session.scalar(select(Document).where(Document.url == doc.file_url))
                    if seen is not None and not dry_run:
                        backfill_site_name(seen, p.site_name)
                if seen is not None:
                    out.already += 1
                    echo(f"  skip  {label} (already ingested)")
                    continue
                if dry_run:
                    echo(f"  would fetch  {label}")
                    continue
                try:
                    resp = client.get(doc.file_url)
                    resp.raise_for_status()
                except (httpx.HTTPError, RobotsDisallowed) as exc:
                    out.failed.append(f"permit {p.permit_number} {label}: {exc}")
                    echo(f"  FAIL  {label}: {exc}")
                    continue
                with session_scope(engine) as session:
                    result = ingest_tceq_air(
                        session,
                        resp.content,
                        raw_dir=settings.raw_dir,
                        url=doc.file_url,
                        content_type=resp.headers.get("content-type"),
                        site_name_hint=p.site_name,
                    )
                    out.fetched += int(result.created)
                    echo(report_line(result, label))
    return out


# --- Comptroller registry -------------------------------------------------------------


def import_registry_page(session, settings: Settings, content: bytes, content_type: str | None):
    stored = save_raw(
        content,
        source=REGISTRY_SOURCE,
        raw_dir=settings.raw_dir,
        ext=".html",
        content_type=content_type,
    )
    entries = parse_registry(content.decode("utf-8", errors="replace"))
    doc = session.scalar(select(Document).where(Document.sha256 == stored.sha256))
    if doc is None:
        doc = Document(
            source=REGISTRY_SOURCE,
            url=REGISTRY_URL,
            sha256=stored.sha256,
            local_path=str(stored.path),
            content_type=content_type,
            size_bytes=stored.size,
        )
        session.add(doc)
        session.flush()
    return import_registry(session, entries, doc), entries


@dataclass
class RegistryResult:
    entries: int
    report: ImportReport


def pull_registry(
    settings: Settings,
    engine: Engine,
    echo: Echo,
    *,
    content: bytes | None = None,
    content_type: str | None = "text/html",
) -> RegistryResult:
    if content is None:
        with PoliteClient(settings) as client:
            resp = client.get(REGISTRY_URL)
            resp.raise_for_status()
        content, content_type = resp.content, resp.headers.get("content-type")
    with session_scope(engine) as session:
        report, entries = import_registry_page(session, settings, content, content_type)
        echo(
            f"{len(entries)} registry entries: {len(report.new)} new, "
            f"{len(report.changed)} changed, {report.unchanged} unchanged, "
            f"{len(report.dropped)} no longer listed"
        )
        for m in report.new:
            if m.how == "new":
                continue
            echo(f"  match ({m.how}, {m.confidence})  {m.entry.name}  ->  {m.site.name}")
            echo(f"        {m.note}")
        created = sum(1 for m in report.new if m.how == "new")
        echo(f"  {created} entries became new sites (no existing site matched)")
        for e in report.changed:
            echo(f"  changed  {e.registration_id} {e.name}")
        for sig in report.dropped:
            echo(f"  dropped  {sig.permit_number} {sig.title}")
        if report.review:
            echo(f"  {len(report.review)} possible matches left for review (low confidence):")
            for line in report.review:
                echo(f"    {line}")
    return RegistryResult(len(entries), report)


# --- Chapter 312 tax abatements ------------------------------------------------------


def _snapshot(session, settings: Settings, content: bytes, *, source, url, ext, content_type):
    stored = save_raw(
        content, source=source, raw_dir=settings.raw_dir, ext=ext, content_type=content_type
    )
    doc = session.scalar(select(Document).where(Document.sha256 == stored.sha256))
    if doc is None:
        doc = Document(
            source=source,
            url=url,
            sha256=stored.sha256,
            local_path=str(stored.path),
            content_type=content_type,
            size_bytes=stored.size,
        )
        session.add(doc)
        session.flush()
    return doc


def import_abatement_file(session, settings: Settings, content: bytes) -> AbatementReport:
    doc = _snapshot(
        session,
        settings,
        content,
        source=ABATEMENT_SOURCE,
        url=ABATEMENT_URL,
        ext=".csv",
        content_type="text/csv",
    )
    return import_abatements(session, parse_detail(content), doc)


def pull_abatements(
    settings: Settings, engine: Engine, echo: Echo, *, content: bytes | None = None
) -> AbatementReport:
    if content is None:
        with PoliteClient(settings) as client:
            resp = client.get(ABATEMENT_URL)
            resp.raise_for_status()
        content = resp.content
    with session_scope(engine) as session:
        report = import_abatement_file(session, settings, content)
        echo(
            f"{report.agreements} abatement agreements, {report.data_centers} data centers: "
            f"{len(report.new)} new, {len(report.changed)} changed, {report.unchanged} unchanged"
        )
        for m in report.new:
            where = m.site.name if m.site else "no site (review)"
            echo(f"  {m.how:<11} {m.abatement.record_id} {m.abatement.zone}  ->  {where}")
            if m.note:
                echo(f"              {m.note}")
        for line in report.review:
            echo(f"  review  {line}")
    return report


# --- TCEQ Central Registry ------------------------------------------------------------


def import_central_registry_file(session, settings: Settings, content: bytes) -> CRReport:
    snap = cr.Snapshot.from_bytes(content)
    doc = _snapshot(
        session,
        settings,
        content,
        source=cr.SOURCE,
        url=cr.PAGE_URL,
        ext=".json",
        content_type="application/json",
    )
    return import_central_registry(session, cr.parse_rows(snap.rows), doc)


def fetch_central_registry(settings: Settings, engine: Engine, echo: Echo) -> bytes:
    """Data center rows from every region, plus every row for RNs already tracked."""
    with session_scope(engine) as session:
        rns = [rn for site in session.scalars(select(Site)) for rn in site.tceq_rns or []]
    urls = cr.data_center_urls() + cr.rn_urls(rns)
    rows: list[dict] = []
    with PoliteClient(settings) as client:
        for url in urls:
            resp = client.get(url)
            resp.raise_for_status()
            batch = resp.json()
            if len(batch) >= cr.ROW_LIMIT:
                raise RuntimeError(f"Central Registry query hit the {cr.ROW_LIMIT} row limit")
            dataset = url.split("/resource/")[1].split(".json")[0]
            rows += [{**r, "_dataset": dataset} for r in cr.drop_individuals(batch)]
    echo(f"{len(urls)} Central Registry queries, {len(rows)} rows")
    # Same row from two queries (a data center that is also a tracked RN): keep one.
    unique = {json.dumps(r, sort_keys=True): r for r in rows}
    return cr.Snapshot(
        urls, sorted(unique.values(), key=lambda r: json.dumps(r, sort_keys=True))
    ).to_bytes()


def pull_central_registry(
    settings: Settings, engine: Engine, echo: Echo, *, content: bytes | None = None
) -> CRReport:
    if content is None:
        content = fetch_central_registry(settings, engine, echo)
    with session_scope(engine) as session:
        report = import_central_registry_file(session, settings, content)
        echo(
            f"{report.entities} TCEQ sites in the pull, {report.data_centers} data centers: "
            f"{len(report.matched)} newly placed, {report.new_signals} new signals, "
            f"{report.changed} changed"
        )
        for m in report.matched:
            echo(f"  {m.how:<11} {m.entity.rn} {m.entity.name}  ->  {m.site.name}")
            if m.note:
                echo(f"              {m.note}")
        for name in report.to_construction:
            echo(f"  under construction  {name}")
        for line in report.review:
            echo(f"  review  {line}")
    return report


# --- Owner lookups --------------------------------------------------------------------


def store_lookup(session, settings: Settings, lookup: Lookup) -> str:
    content = lookup.to_bytes()
    stored = save_raw(
        content,
        source=FRANCHISE_SOURCE,
        raw_dir=settings.raw_dir,
        ext=".json",
        content_type="application/json",
    )
    if session.scalar(select(Document).where(Document.sha256 == stored.sha256)) is None:
        session.add(
            Document(
                source=FRANCHISE_SOURCE,
                url=lookup.url,
                sha256=stored.sha256,
                local_path=str(stored.path),
                content_type="application/json",
                size_bytes=stored.size,
            )
        )
        session.flush()
    return apply_lookup(session, lookup)


@dataclass
class OwnersResult:
    names: int = 0
    found: int = 0
    addresses: int = 0
    failed: list[str] = field(default_factory=list)


def lookup_owners(
    settings: Settings,
    engine: Engine,
    echo: Echo,
    *,
    refresh: bool = False,
    max_names: int = 100,
) -> OwnersResult:
    """Franchise tax lookups for entities not yet looked up, then their addresses.

    Each lookup commits on its own, so a failure partway keeps what was done."""
    out = OwnersResult()
    with PoliteClient(settings) as client:
        with session_scope(engine) as session:
            done = saved_lookups(session)
            names = [
                e.legal_name
                for e in lookup_targets(session)
                if refresh or ("name", entity_key(e.legal_name)) not in done
            ][:max_names]
        out.names = len(names)
        echo(f"Looking up {len(names)} names")
        for name in names:
            url = name_query_url(name)
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except (httpx.HTTPError, RobotsDisallowed) as exc:
                out.failed.append(f"{name}: {exc}")
                echo(f"  FAIL  {name}: {exc}")
                continue
            with session_scope(engine) as session:
                status = store_lookup(session, settings, Lookup("name", name, url, resp.json()))
            out.found += status.startswith("found")
            echo(f"  {name}: {status}")

        with session_scope(engine) as session:
            done = saved_lookups(session)
            addresses = set()
            for e in lookup_targets(session):
                lookup = done.get(("name", entity_key(e.legal_name)))
                if not (e.taxpayer_number and lookup):
                    continue
                for row in lookup.rows:
                    if row.get("taxpayer_number") == e.taxpayer_number:
                        addresses.add(
                            (row.get("taxpayer_address", ""), row.get("taxpayer_zip", ""))
                        )
        for address, zip_code in sorted(addresses):
            query = f"{address}|{zip_code}"
            if not refresh and ("address", query) in done:
                continue
            url = address_query_url(address, zip_code, limit=AGENT_OFFICE_MIN * 2)
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except (httpx.HTTPError, RobotsDisallowed) as exc:
                out.failed.append(f"{query}: {exc}")
                echo(f"  FAIL  {query}: {exc}")
                continue
            with session_scope(engine) as session:
                status = store_lookup(session, settings, Lookup("address", query, url, resp.json()))
            out.addresses += 1
            echo(f"  {status}")
    return out
