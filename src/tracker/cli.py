"""`tracker` command line."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from tracker.collectors.tceq.air_permits import Fetched, fetch_seeds, read_seeds
from tracker.config import PILOT_COUNTIES, get_settings
from tracker.db.models import Equipment, Site
from tracker.db.session import init_db, make_engine, session_scope
from tracker.http import PoliteClient
from tracker.ingest import IngestResult, ingest_tceq_air

app = typer.Typer(help="Texas data center tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
pull_app = typer.Typer(help="Collect documents from a source.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(pull_app, name="pull")


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


@app.command("equipment")
def equipment() -> None:
    """Show generator and turbine counts per site."""
    engine = _engine()
    with session_scope(engine) as session:
        rows = session.execute(
            select(Site.name, Site.county, Equipment).join(Equipment, Equipment.site_id == Site.id)
        ).all()
        if not rows:
            typer.echo("No equipment yet. Run `tracker ingest` or `tracker pull tceq`.")
            return
        by_site: dict[tuple[str, str | None], list[Equipment]] = defaultdict(list)
        for name, county, eq in rows:
            by_site[(name, county)].append(eq)
        for (name, county), items in sorted(by_site.items()):
            typer.echo(f"{name} ({county or '?'} County)")
            for e in items:
                size = f"{e.unit_size:g} {e.unit}" if e.unit_size else "size ?"
                make = " ".join(x for x in (e.manufacturer, e.model) if x) or "make ?"
                typer.echo(f"  {e.count:>4} x {e.category:<17} {size:<12} {make}  (p.{e.page})")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    app()


if __name__ == "__main__":
    main()
