"""Hand-entered facts, each with a public source, for what the automatic sources miss.

Two seed files, re-applied on every rebuild:

- seeds/announced_projects.csv: a project made public by a notice or report before any state
  filing names it as a data center (SpaceX's Starbase "super-compute facility" is in a city
  hearing notice; TCEQ files SpaceX under its rocket factory). Each row becomes a site, if
  no site has that name yet, with a NOTICE signal that links to the source, and the
  company named in the notice is tied to it at medium confidence (from a report or notice,
  not a permit).
- seeds/site_places.csv: a county or city for a tracked site whose own filings give none
  (the Comptroller registry lists "Giga Texas Datacenter" with no address).

Companies only: never put a person's name in either file. Every row needs a source URL.
When real filings for an announced project arrive, they attach to the same site by name or
by review, and the notice stays as its first signal.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import (
    Confidence,
    Extraction,
    LinkType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.entities import EntityBook, is_organization, link_party

SEEDS = Path(__file__).resolve().parents[2] / "seeds"
PROJECTS_PATH = SEEDS / "announced_projects.csv"
PLACES_PATH = SEEDS / "site_places.csv"
SOURCE = "public_notice"
EXTRACTOR = "announced_projects"
EXTRACTOR_VERSION = "0.1.0"
ROLES = {"owner": LinkType.OWNS, "developer": LinkType.DEVELOPS, "operator": LinkType.OPERATES}


class SeedError(ValueError):
    pass


@dataclass
class Announced:
    site: str
    county: str | None
    city: str | None
    company: str
    role: str
    status: SiteStatus
    date: date
    title: str
    source_url: str
    note: str


@dataclass
class Place:
    site: str
    county: str | None
    city: str | None
    source_url: str
    note: str


def _rows(path: Path) -> list[tuple[int, dict]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [(i, r) for i, r in enumerate(csv.DictReader(f), start=2) if (r.get("site") or "")]


def _opt(row: dict, key: str) -> str | None:
    return (row.get(key) or "").strip() or None


def _source(path: Path, i: int, row: dict) -> str:
    url = (row.get("source_url") or "").strip()
    if not url.startswith("http"):
        raise SeedError(f"{path}:{i}: every row needs a source_url")
    return url


def read_projects(path: Path = PROJECTS_PATH) -> list[Announced]:
    out = []
    for i, row in _rows(path):
        company = row["company"].strip()
        if not is_organization(company):
            raise SeedError(f"{path}:{i}: company {company!r} is not a company name")
        role = (row.get("role") or "owner").strip()
        if role not in ROLES:
            raise SeedError(f"{path}:{i}: role must be one of {', '.join(ROLES)}")
        out.append(
            Announced(
                site=row["site"].strip(),
                county=_opt(row, "county"),
                city=_opt(row, "city"),
                company=company,
                role=role,
                status=SiteStatus((row.get("status") or "filed").strip()),
                date=date.fromisoformat(row["date"].strip()),
                title=row["title"].strip(),
                source_url=_source(path, i, row),
                note=(row.get("note") or "").strip(),
            )
        )
    return out


def read_places(path: Path = PLACES_PATH) -> list[Place]:
    return [
        Place(
            site=row["site"].strip(),
            county=_opt(row, "county"),
            city=_opt(row, "city"),
            source_url=_source(path, i, row),
            note=(row.get("note") or "").strip(),
        )
        for i, row in _rows(path)
    ]


def _site_named(session: Session, name: str) -> Site | None:
    return session.scalars(select(Site).where(Site.name == name).order_by(Site.id)).first()


def apply_projects(session: Session, projects: list[Announced]) -> int:
    """Add each announced project once. Returns how many notices were new."""
    book = EntityBook(session)
    added = 0
    for p in projects:
        site = _site_named(session, p.site)
        if site is None:
            site = Site(name=p.site, county=p.county, city=p.city)
            site.set_status(p.status, note=f"announced: {p.title}")
            session.add(site)
            session.flush()
        exists = session.scalars(
            select(Signal).where(
                Signal.source == SOURCE, Signal.site_id == site.id, Signal.title == p.title
            )
        ).first()
        if exists:
            continue
        fields = {"url": p.source_url, "company": p.company, "role": p.role, "note": p.note}
        signal = Signal(
            site=site,
            type=SignalType.NOTICE,
            source=SOURCE,
            title=p.title,
            filing_date=p.date,
            raw_text=json.dumps(fields),
        )
        session.add(signal)
        session.add(
            Extraction(
                signal=signal,
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                fields=fields,
            )
        )
        link_party(
            session,
            book,
            p.company,
            site,
            ROLES[p.role],
            Confidence.MEDIUM,
            evidence_url=p.source_url,
            note=f"named in: {p.title}",
        )
        added += 1
    session.flush()
    return added


def apply_places(session: Session, places: list[Place]) -> list[str]:
    """Fill a missing county or city. Returns site names that were not found."""
    missing = []
    for p in places:
        site = _site_named(session, p.site)
        if site is None:
            missing.append(p.site)
            continue
        site.county = site.county or p.county
        site.city = site.city or p.city
    session.flush()
    return missing
