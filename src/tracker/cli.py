"""`tracker` command line."""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Annotated

import httpx
import typer
from sqlalchemy import func, select

from tracker.collectors.comptroller.data_center_registry import REGISTRY_URL, parse_registry
from tracker.collectors.comptroller.data_center_registry import SOURCE as REGISTRY_SOURCE
from tracker.collectors.comptroller.franchise_tax import SOURCE as FRANCHISE_SOURCE
from tracker.collectors.comptroller.franchise_tax import (
    Lookup,
    address_query_url,
    name_query_url,
)
from tracker.collectors.tceq.air_permits import Fetched, fetch_seeds, read_seeds
from tracker.collectors.tceq.records_online import DEFAULT_KEYWORDS, RecordsOnline, plan_discovery
from tracker.config import PILOT_COUNTIES, get_settings
from tracker.db.models import Document, Equipment, LinkType, Signal, Site
from tracker.db.session import init_db, make_engine, session_scope
from tracker.entities import entity_key, site_companies, site_entities
from tracker.http import PoliteClient, RobotsDisallowed
from tracker.ingest import IngestResult, ingest_tceq_air
from tracker.owners import (
    AGENT_OFFICE_MIN,
    apply_findings,
    apply_lookup,
    lookup_targets,
    owner_queue,
    read_findings,
    saved_lookups,
)
from tracker.registry_import import import_registry
from tracker.rollup import site_inventory
from tracker.storage import save_raw

app = typer.Typer(help="Texas data center tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
pull_app = typer.Typer(help="Collect documents from a source.", no_args_is_help=True)
discover_app = typer.Typer(help="Find new documents automatically.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(pull_app, name="pull")
owners_app = typer.Typer(help="Find who is behind the LLCs on filings.", no_args_is_help=True)
app.add_typer(discover_app, name="discover")
app.add_typer(owners_app, name="owners")


def _engine():
    engine = make_engine(get_settings().db_url)
    init_db(engine)
    return engine


def _report(result: IngestResult, label: str) -> None:
    if not result.created:
        typer.echo(f"  skip  {label} (already ingested)")
        return
    eq = result.signal.equipment
    site = result.site.name if result.site else "no RN, unmatched"
    county = result.site.county if result.site else None
    flag = "" if county is None or county in PILOT_COUNTIES else f"  [outside pilot: {county}]"
    typer.echo(f"  ok    {label}: {len(eq)} equipment rows, site: {site}{flag}")


@db_app.command("init")
def db_init() -> None:
    """Create the database tables."""
    _engine()
    typer.echo(f"Database ready at {get_settings().db_url}")


@db_app.command("rebuild")
def db_rebuild() -> None:
    """Re-extract every saved document into a fresh database (no downloads).

    Use after an extractor or schema change. The old database is kept as tracker.db.bak.
    """
    settings = get_settings()
    if not settings.db_url.startswith("sqlite:///"):
        typer.echo("rebuild only supports the default SQLite database", err=True)
        raise typer.Exit(1)
    path = Path(settings.db_url.removeprefix("sqlite:///"))
    if not path.exists():
        typer.echo(f"No database at {path}; nothing to rebuild.")
        return
    with sqlite3.connect(path) as old:
        cols = {row[1] for row in old.execute("PRAGMA table_info(document)")}
        hint = "listed_site_name" if "listed_site_name" in cols else "NULL"
        docs = old.execute(
            f"SELECT url, local_path, content_type, {hint}, source FROM document ORDER BY id"
        ).fetchall()
    backup = path.with_name(path.name + ".bak")
    path.replace(backup)
    engine = _engine()
    typer.echo(f"Rebuilding from {len(docs)} saved documents (old database: {backup})")
    # Registry snapshots after TCEQ documents, so they match against every TCEQ site as on a
    # live pull; then owner lookups, which need the entities both create.
    order = {REGISTRY_SOURCE: 1, FRANCHISE_SOURCE: 2}
    docs.sort(key=lambda d: order.get(d[4], 0))
    for url, local_path, content_type, site_hint, source in docs:
        raw = Path(local_path)
        if not raw.exists():
            typer.echo(f"  MISSING  {raw} ({url})")
            continue
        if source == REGISTRY_SOURCE:
            with session_scope(engine) as session:
                report, entries = _import_registry_page(
                    session, settings, raw.read_bytes(), content_type
                )
            typer.echo(f"  registry  {len(entries)} entries, {len(report.new)} new ({raw.name})")
            continue
        if source == FRANCHISE_SOURCE:
            with session_scope(engine) as session:
                _store_lookup(session, settings, Lookup.from_bytes(raw.read_bytes()))
            continue
        with session_scope(engine) as session:
            result = ingest_tceq_air(
                session,
                raw.read_bytes(),
                raw_dir=settings.raw_dir,
                url=url,
                filename=None if url else raw.name,
                content_type=content_type,
                site_name_hint=site_hint,
            )
            _report(result, url or raw.name)
    with session_scope(engine) as session:
        missing = apply_findings(session, read_findings())
    for name in missing:
        typer.echo(f"  owner finding for unknown entity: {name}")


def _backfill_site_name(session, document: Document, listed_name: str | None) -> None:
    """Record the search listing's site name and use it for a site the documents never named."""
    if not listed_name:
        return
    if document.listed_site_name is None:
        document.listed_site_name = listed_name
    for sig in document.signals:
        if sig.site is not None and sig.site.name.startswith("Unnamed site "):
            sig.site.aliases = [*sig.site.aliases, sig.site.name]
            sig.site.name = listed_name


@pull_app.command("tceq")
def pull_tceq(
    seeds: Annotated[Path, typer.Option(help="CSV of document URLs.")] = Path("seeds/tceq_air.csv"),
    limit: Annotated[int, typer.Option(help="Stop after N documents (0 = all).")] = 0,
) -> None:
    """Download TCEQ air permit documents from the seed list and extract equipment."""
    settings = get_settings()
    seed_list = read_seeds(seeds)
    if limit:
        seed_list = seed_list[:limit]
    pace = f"1 request / {settings.min_request_interval:g}s"
    typer.echo(f"Pulling {len(seed_list)} TCEQ documents ({pace})")
    engine = _engine()
    failures = 0
    with PoliteClient(settings) as client:
        for item in fetch_seeds(client, seed_list):
            if not isinstance(item, Fetched):
                seed, exc = item
                failures += 1
                typer.echo(f"  FAIL  {seed.url}: {exc}")
                continue
            with session_scope(engine) as session:
                result = ingest_tceq_air(
                    session,
                    item.content,
                    raw_dir=settings.raw_dir,
                    url=item.seed.url,
                    content_type=item.content_type,
                )
                _report(result, item.seed.url)
    if failures == len(seed_list) and seed_list:
        typer.echo("Every request failed. Check network access to tceq.texas.gov.", err=True)
        raise typer.Exit(1)


def _import_registry_page(session, settings, content: bytes, content_type: str | None):
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


@pull_app.command("comptroller")
def pull_comptroller(
    page: Annotated[
        Path | None, typer.Option(help="Parse a saved copy of the page instead of fetching.")
    ] = None,
) -> None:
    """Pull the Comptroller's registry of data centers certified for the sales tax exemption."""
    settings = get_settings()
    engine = _engine()
    if page is not None:
        content, content_type = page.read_bytes(), "text/html"
    else:
        with PoliteClient(settings) as client:
            resp = client.get(REGISTRY_URL)
        content, content_type = resp.content, resp.headers.get("content-type")
    with session_scope(engine) as session:
        report, entries = _import_registry_page(session, settings, content, content_type)
        typer.echo(
            f"{len(entries)} registry entries: {len(report.new)} new, "
            f"{len(report.changed)} changed, {report.unchanged} unchanged, "
            f"{len(report.dropped)} no longer listed"
        )
        for m in report.new:
            if m.how == "new":
                continue
            typer.echo(f"  match ({m.how}, {m.confidence})  {m.entry.name}  ->  {m.site.name}")
            typer.echo(f"        {m.note}")
        created = sum(1 for m in report.new if m.how == "new")
        typer.echo(f"  {created} entries became new sites (no existing site matched)")
        for e in report.changed:
            typer.echo(f"  changed  {e.registration_id} {e.name}")
        for sig in report.dropped:
            typer.echo(f"  dropped  {sig.permit_number} {sig.title}")
        if report.review:
            typer.echo(f"  {len(report.review)} possible matches left for review (low confidence):")
            for line in report.review:
                typer.echo(f"    {line}")


@discover_app.command("tceq")
def discover_tceq(
    keyword: Annotated[
        list[str] | None,
        typer.Option("--keyword", "-k", help="Site-name keyword (repeatable)."),
    ] = None,
    since: Annotated[
        str, typer.Option(help="Skip permits with no documents on or after this date.")
    ] = "2022-01-01",
    max_permits: Annotated[int, typer.Option(help="Stop after N permits (0 = all).")] = 0,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List what would be fetched; download nothing.")
    ] = False,
) -> None:
    """Search TCEQ Records Online for data center permits and ingest their key documents."""
    settings = get_settings()
    keywords = tuple(keyword) if keyword else DEFAULT_KEYWORDS
    engine = _engine()
    with PoliteClient(settings) as client:
        ro = RecordsOnline(client)
        typer.echo(f"Searching site names for {', '.join(keywords)} (since {since})")
        plan = plan_discovery(
            ro, keywords, since=date.fromisoformat(since), max_permits=max_permits
        )
        for permit, name in plan.skipped_construction:
            typer.echo(f"  skip  permit {permit} {name} (construction site, not a data center)")
        n_docs = sum(len(p.documents) for p in plan.permits)
        typer.echo(f"{len(plan.permits)} permits, {n_docs} documents to check")

        for p in plan.permits:
            typer.echo(f"permit {p.permit_number}  {p.site_name or '?'}")
            if not p.documents:
                typer.echo("  none  no technical review or final action listed")
            for doc in p.documents:
                label = f"{doc.title} {doc.begin_date or '?'} (content {doc.content_id})"
                with session_scope(engine) as session:
                    seen = session.scalar(select(Document).where(Document.url == doc.file_url))
                    if seen is not None and not dry_run:
                        _backfill_site_name(session, seen, p.site_name)
                if seen is not None:
                    typer.echo(f"  skip  {label} (already ingested)")
                    continue
                if dry_run:
                    typer.echo(f"  would fetch  {label}")
                    continue
                try:
                    resp = client.get(doc.file_url)
                except (httpx.HTTPError, RobotsDisallowed) as exc:
                    typer.echo(f"  FAIL  {label}: {exc}")
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
                    _report(result, label)


@app.command("ingest")
def ingest(
    files: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Ingest TCEQ air permit files you downloaded by hand."""
    settings = get_settings()
    engine = _engine()
    for path in files:
        with session_scope(engine) as session:
            result = ingest_tceq_air(
                session, path.read_bytes(), raw_dir=settings.raw_dir, filename=path.name
            )
            _report(result, path.name)


def _equipment_line(e: Equipment, size_from: Equipment | None = None) -> str:
    src = size_from or e
    size = f"{src.unit_size:g} {src.unit}" if src.unit_size else "size ?"
    if size_from is not None:
        size += "*"
    make = " ".join(x for x in (e.manufacturer, e.model) if x) or "make ?"
    return f"  {e.count:>4} x {e.category:<17} {size:<12} {make}  (p.{e.page}, {e.method})"


@app.command("equipment")
def equipment(
    all_rows: Annotated[
        bool, typer.Option("--all", help="Every row from every document, not just current.")
    ] = False,
) -> None:
    """Show current generator and turbine counts per site, counting each unit once."""
    engine = _engine()
    with session_scope(engine) as session:
        if all_rows:
            rows = session.execute(
                select(Site.name, Site.county, Equipment).join(
                    Equipment, Equipment.site_id == Site.id
                )
            ).all()
            by_site: dict[tuple[str, str | None], list[Equipment]] = defaultdict(list)
            for name, county, eq in rows:
                by_site[(name, county)].append(eq)
            for (name, county), items in sorted(by_site.items()):
                typer.echo(f"{name} ({county or '?'} County)")
                for e in items:
                    typer.echo(_equipment_line(e))
            return

        found = False
        for site in session.scalars(select(Site).order_by(Site.county, Site.name)):
            inv = site_inventory(session, site)
            if not inv.permits and not inv.withdrawn_permits:
                continue
            found = True
            typer.echo(f"{site.name} ({site.county or '?'} County)")
            for p in inv.permits:
                when = p.signal.filing_date.isoformat() if p.signal.filing_date else "date ?"
                others = (
                    f", {p.other_documents} other docs not counted" if p.other_documents else ""
                )
                typer.echo(f" permit {p.permit_number or '?'}: from {when} document{others}")
                for e in p.rows:
                    mark = {1: "+", -1: "-", 0: " "}[p.sign(e)]
                    line = _equipment_line(e, p.borrowed_sizes.get(e.id))
                    typer.echo(f"{mark}{line[1:]}  [{e.role or 'unknown'}]")
                if p.borrowed_sizes:
                    dates = {
                        b.signal.filing_date.isoformat() if b.signal.filing_date else "?"
                        for b in p.borrowed_sizes.values()
                    }
                    typer.echo(f"   * size from this permit's {', '.join(sorted(dates))} document")
            for permit_number in inv.withdrawn_permits:
                typer.echo(f" permit {permit_number}: application withdrawn, not counted")
            for category, t in sorted(inv.totals().items()):
                mw = f", {t['mw']:,.1f} MW" if t["mw"] else ""
                typer.echo(f" total {category}: {t['count']:g} units{mw}")
        if not found:
            typer.echo("No equipment yet. Run `tracker ingest` or `tracker pull tceq`.")


ROLE_LABEL = {LinkType.FILED_BY: "permit holder"}


@app.command("sites")
def sites(
    linked: Annotated[
        bool, typer.Option("--linked", help="Only sites with two or more kinds of signal.")
    ] = False,
) -> None:
    """List sites: sources, permits, equipment rows, companies, and NSR permits to fetch."""
    engine = _engine()
    with session_scope(engine) as session:
        all_sites = session.scalars(select(Site).order_by(Site.county, Site.name)).all()
        if not all_sites:
            typer.echo("No sites yet. Run `tracker ingest` or `tracker pull tceq`.")
            return
        shown = 0
        for site in all_sites:
            signals = session.scalars(select(Signal).where(Signal.site_id == site.id)).all()
            kinds = sorted({str(s.type) for s in signals})
            if linked and len(kinds) < 2:
                continue
            shown += 1
            permits = sorted({s.permit_number for s in signals if s.permit_number})
            n_equipment = session.scalar(
                select(func.count()).select_from(Equipment).where(Equipment.site_id == site.id)
            )
            groups: set[str] = set()
            nsr: set[str] = set()
            for sig in signals:
                for ex in sig.extractions:
                    groups.update(g["unit_id"] for g in ex.fields.get("unit_groups", []))
                    nsr.update(a["number"] for a in ex.fields.get("nsr_authorizations", []))
            rns = ", ".join(site.tceq_rns) or "no RN"
            typer.echo(f"{site.name} ({site.county or '?'} County) [{site.status}] {rns}")
            typer.echo(f"  permits: {', '.join(permits) or '-'}   equipment rows: {n_equipment}")
            if groups:
                typer.echo(f"  unit groups (counts unknown): {', '.join(sorted(groups))}")
            if nsr:
                typer.echo(f"  NSR permits referenced: {', '.join(sorted(nsr))}")
            typer.echo(f"  signals: {', '.join(kinds) or '-'}")
            holders = [
                f"{name} [{conf}]"
                for role, name, conf in site_entities(session, site)
                if role == LinkType.FILED_BY
            ]
            if holders:
                typer.echo(f"  permit holders: {'; '.join(holders)}")
            companies = site_companies(session, site)
            if companies:
                parts = [
                    f"{ROLE_LABEL.get(role, role)} {name}"
                    + (f" ({ticker})" if ticker else "")
                    + f" [{conf}]"
                    for role, name, ticker, conf in companies
                ]
                typer.echo(f"  companies: {'; '.join(parts)}")
        if linked:
            typer.echo(f"{shown} sites with two or more kinds of signal")


def _store_lookup(session, settings, lookup: Lookup) -> str:
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


@owners_app.command("lookup")
def owners_lookup(
    refresh: Annotated[bool, typer.Option(help="Look up names already looked up.")] = False,
    max_names: Annotated[int, typer.Option(help="Stop after this many name lookups.")] = 100,
) -> None:
    """Look up permit holders and registry parties in the franchise tax file (free, official).

    Then look up who else uses each one's mailing address, to find sister LLCs.
    """
    settings = get_settings()
    engine = _engine()
    with PoliteClient(settings) as client, session_scope(engine) as session:
        done = saved_lookups(session)
        names = [
            e.legal_name
            for e in lookup_targets(session)
            if refresh or ("name", entity_key(e.legal_name)) not in done
        ][:max_names]
        typer.echo(f"Looking up {len(names)} names")
        for name in names:
            url = name_query_url(name)
            rows = client.get(url).json()
            status = _store_lookup(session, settings, Lookup("name", name, url, rows))
            typer.echo(f"  {name}: {status}")
        done = saved_lookups(session)
        addresses = set()
        for e in lookup_targets(session):
            lookup = done.get(("name", entity_key(e.legal_name)))
            if not (e.taxpayer_number and lookup):
                continue
            for row in lookup.rows:
                if row.get("taxpayer_number") == e.taxpayer_number:
                    addresses.add((row.get("taxpayer_address", ""), row.get("taxpayer_zip", "")))
        for address, zip_code in sorted(addresses):
            query = f"{address}|{zip_code}"
            if not refresh and ("address", query) in done:
                continue
            url = address_query_url(address, zip_code, limit=AGENT_OFFICE_MIN * 2)
            rows = client.get(url).json()
            status = _store_lookup(session, settings, Lookup("address", query, url, rows))
            typer.echo(f"  {status}")


@owners_app.command("queue")
def owners_queue(
    all_items: Annotated[
        bool, typer.Option("--all", help="Include entities with a medium company link.")
    ] = False,
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Also write the queue to this CSV file.")
    ] = None,
) -> None:
    """Entities on sites with no confirmed company, what is known, and the next hand step.

    Sites with an air permit come first, then entities on the most sites."""
    engine = _engine()
    with session_scope(engine) as session:
        items = owner_queue(session)
        if csv_path is not None:
            _write_queue_csv(csv_path, items, all_items)
        shown = 0
        for item in items:
            if item.company_confidence is not None and not all_items:
                continue
            shown += 1
            e = item.entity
            sites = "; ".join(s.name for s in item.sites)
            typer.echo(f"{e.legal_name}  [{item.status}]")
            typer.echo(f"  sites: {sites}")
            if e.taxpayer_number:
                formed = e.formation_date.isoformat() if e.formation_date else "?"
                office = "  (registered agent office)" if item.agent_office else ""
                typer.echo(f"  SOS file {e.sos_file_number}, formed {formed}, {e.address}{office}")
            for name, conf in item.affiliates:
                typer.echo(f"  same address: {name} [{conf}]")
            typer.echo(f"  next: {item.next_step}")
        typer.echo(f"{shown} entities without a known company")


def _write_queue_csv(path: Path, items, all_items: bool) -> None:
    import csv

    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "entity",
                "status",
                "air_permit",
                "sites",
                "sos_file_number",
                "formed",
                "address",
                "agent_office",
                "same_address",
                "next_step",
            ]
        )
        for i in items:
            if i.company_confidence is not None and not all_items:
                continue
            e = i.entity
            w.writerow(
                [
                    e.legal_name,
                    i.status,
                    i.has_air_permit,
                    "; ".join(s.name for s in i.sites),
                    e.sos_file_number or "",
                    e.formation_date or "",
                    e.address or "",
                    i.agent_office,
                    "; ".join(f"{n} [{c}]" for n, c in i.affiliates),
                    i.next_step,
                ]
            )
    typer.echo(f"Wrote {path}")


@owners_app.command("apply")
def owners_apply() -> None:
    """Apply hand-researched owners from seeds/owner_findings.csv."""
    engine = _engine()
    findings = read_findings()
    with session_scope(engine) as session:
        missing = apply_findings(session, findings)
    typer.echo(f"{len(findings)} findings applied")
    for name in missing:
        typer.echo(f"  no entity named {name!r}")


@app.command("serve")
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    host: Annotated[str, typer.Option(help="Interface; keep 127.0.0.1 unless you mean it.")] = (
        "127.0.0.1"
    ),
) -> None:
    """Serve the JSON API and the built web app (web/dist) at http://127.0.0.1:8000."""
    import uvicorn

    from tracker.api import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command("ticker")
def ticker(symbol: str) -> None:
    """Sites and supplied equipment for one ticker, e.g. `tracker ticker ORCL`."""
    from tracker.views.ticker import ticker_view

    with session_scope(_engine()) as session:
        view = ticker_view(session, symbol)
    if view is None:
        typer.echo(f"No company or supplier with ticker {symbol.upper()}", err=True)
        raise typer.Exit(1)
    d, s = view["demand"], view["supply"]
    typer.echo(f"{view['symbol']} ({view['exchange']}): {', '.join(view['names'])}")
    t = d["totals"]
    typer.echo(
        f"  demand: {t['sites']} sites ({t['high_confidence_sites']} high confidence), "
        f"{t['generators']} generators, {t['turbines']} turbines, {t['mw']} MW"
    )
    for site in d["sites"]:
        roles = ", ".join(sorted({x["role"] for x in site["ties"]}))
        p = site["power"]
        typer.echo(
            f"    [{site['confidence']}] {site['name']} ({site['county'] or '?'}): {roles}; "
            f"{p['generators']} gen, {p['turbines']} turb, {p['mw']} MW"
        )
    for site in d["leads"]:
        typer.echo(f"    [lead] {site['name']}")
    t = s["totals"]
    if s["makers"]:
        typer.echo(
            f"  supply ({', '.join(s['makers'])}): {t['generators']} generators, "
            f"{t['turbines']} turbines, {t['mw']} MW"
        )
        for site in s["sites"]:
            typer.echo(
                f"    {site['name']}: {site['generators']} gen, {site['turbines']} turb, "
                f"{site['mw']} MW"
            )
    typer.echo("Not investment advice.")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    app()


if __name__ == "__main__":
    main()
