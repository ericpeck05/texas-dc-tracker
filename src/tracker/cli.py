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

from tracker.collectors.tceq.air_permits import Fetched, fetch_seeds, read_seeds
from tracker.collectors.tceq.records_online import DEFAULT_KEYWORDS, RecordsOnline, plan_discovery
from tracker.config import PILOT_COUNTIES, get_settings
from tracker.db.models import Document, Equipment, Signal, Site
from tracker.db.session import init_db, make_engine, session_scope
from tracker.http import PoliteClient, RobotsDisallowed
from tracker.ingest import IngestResult, ingest_tceq_air
from tracker.rollup import site_inventory

app = typer.Typer(help="Texas data center tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
pull_app = typer.Typer(help="Collect documents from a source.", no_args_is_help=True)
discover_app = typer.Typer(help="Find new documents automatically.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(pull_app, name="pull")
app.add_typer(discover_app, name="discover")


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
            f"SELECT url, local_path, content_type, {hint} FROM document ORDER BY id"
        ).fetchall()
    backup = path.with_name(path.name + ".bak")
    path.replace(backup)
    engine = _engine()
    typer.echo(f"Rebuilding from {len(docs)} saved documents (old database: {backup})")
    for url, local_path, content_type, site_hint in docs:
        raw = Path(local_path)
        if not raw.exists():
            typer.echo(f"  MISSING  {raw} ({url})")
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


@app.command("sites")
def sites() -> None:
    """List sites with their permits, equipment rows, unit groups, and NSR permits to fetch."""
    engine = _engine()
    with session_scope(engine) as session:
        all_sites = session.scalars(select(Site).order_by(Site.county, Site.name)).all()
        if not all_sites:
            typer.echo("No sites yet. Run `tracker ingest` or `tracker pull tceq`.")
            return
        for site in all_sites:
            signals = session.scalars(select(Signal).where(Signal.site_id == site.id)).all()
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


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    app()


if __name__ == "__main__":
    main()
