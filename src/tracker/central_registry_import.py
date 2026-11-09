"""Load TCEQ Central Registry sites: RNs, counties, stormwater and air authorizations.

Each regulated entity (RN) that is a data center, or that a tracked site already has, is
matched to a site, in order:

1. RN (high): the site already has this RN from an air permit document.
2. Owner (medium): the principal on the TCEQ customer record is an entity tied to exactly
   one site, and the site isn't placed in another county.
3. Owner and place or name (medium): the principal is on several sites, and exactly one is
   in this county or shares the RN's distinctive name words ("Austin Data Center 2").
4. Name (medium): no owner match, and the RN's distinctive name words match exactly one
   site in a compatible county.

Otherwise the RN becomes its own site, with low-confidence SAME_SITE review links to any
owner candidates in the same county. A matched site gains the RN and, if it had none, the
county.

Each construction or industrial stormwater authorization becomes a STORMWATER signal, and
each air NSR authorization not already known from a permit document becomes an AIR_PERMIT
signal. An active construction stormwater authorization (TXR15...) means dirt is moving,
so an early-stage site moves to under construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.tceq.central_registry import (
    SOURCE,
    ProgramId,
    RegulatedEntity,
    data_center_reason,
)
from tracker.db.models import (
    Confidence,
    Document,
    Entity,
    Extraction,
    Link,
    LinkMethod,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.entities import EntityBook, add_link, entity_key, link_party
from tracker.reference.companies import find_company
from tracker.registry_import import name_tokens

EXTRACTOR = "tceq_central_registry_rows"
EXTRACTOR_VERSION = "0.1.0"
SIGNAL_TYPE = {"STORM": SignalType.STORMWATER, "AIRNSR": SignalType.AIR_PERMIT}
PROGRAM_LABEL = {"STORM": "Stormwater", "AIRNSR": "Air NSR authorization"}
RECENT_DAYS = 3 * 365
BEFORE_CONSTRUCTION = {None, SiteStatus.RUMORED, SiteStatus.FILED, SiteStatus.APPROVED}


def evidence_url(ent: RegulatedEntity) -> str:
    dataset = ent.dataset or "msah-s2rv"
    return f"https://data.texas.gov/resource/{dataset}.json?ref_num_txt={ent.rn}"


@dataclass
class CRMatch:
    entity: RegulatedEntity
    site: Site
    how: str  # rn, owner, owner+place, name, new
    note: str
    signals: int = 0


@dataclass
class CRReport:
    entities: int = 0  # RNs in the pull
    data_centers: int = 0  # RNs kept
    matched: list[CRMatch] = field(default_factory=list)  # first time this RN was placed
    new_signals: int = 0
    changed: int = 0
    review: list[str] = field(default_factory=list)
    to_construction: list[str] = field(default_factory=list)


class _Index:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.by_rn: dict[str, Site] = {}
        self.sites: dict[int, Site] = {}
        self.by_entity: dict[str, set[int]] = {}
        names = {e.id: e.legal_name for e in session.scalars(select(Entity))}
        for site in session.scalars(select(Site)):
            self.add(site)
        for link in session.scalars(
            select(Link).where(Link.from_type == RecordType.ENTITY, Link.to_type == RecordType.SITE)
        ):
            self.by_entity.setdefault(entity_key(names[link.from_id]), set()).add(link.to_id)

    def add(self, site: Site) -> None:
        self.sites[site.id] = site
        for rn in site.tceq_rns or []:
            self.by_rn[rn] = site

    def tokens(self, site: Site) -> set[frozenset[str]]:
        return {t for n in (site.name, *site.aliases) if (t := name_tokens(n))}


def _county_ok(site: Site, county: str | None) -> bool:
    return not (site.county and county and site.county.lower() != county.lower())


def _known_company(name: str) -> str | None:
    found = find_company(name)
    return found[0].name if found else None


def _match(ent: RegulatedEntity, index: _Index) -> tuple[Site | None, str, str, list[Site]]:
    if ent.rn in index.by_rn:
        return index.by_rn[ent.rn], "rn", f"same TCEQ RN {ent.rn}", []
    tokens = name_tokens(ent.name)
    distinctive = bool(tokens) and (len(tokens) > 1 or any(len(w) > 4 for w in tokens))
    named = [
        s
        for s in index.sites.values()
        if distinctive and tokens in index.tokens(s) and _county_ok(s, ent.county)
    ]
    if len(named) == 1:
        site = named[0]
        return site, "name", f"name '{ent.name}' matches '{site.name}'", []
    candidates: list[Site] = []
    if ent.principal:
        ids = index.by_entity.get(entity_key(ent.principal), set())
        candidates = [index.sites[i] for i in sorted(ids) if _county_ok(index.sites[i], ent.county)]
    if len(candidates) == 1:
        site = candidates[0]
        return site, "owner", f"principal '{ent.principal}' is tied to this site", []
    if candidates:
        county = (ent.county or "").lower()
        fits = [s for s in candidates if s.county and s.county.lower() == county]
        if len(fits) == 1:
            site = fits[0]
            note = f"principal '{ent.principal}' is tied to this site; same county"
            return site, "owner+place", note, []
        return None, "new", "", fits
    return None, "new", "", []


def import_central_registry(
    session: Session,
    entities: list[RegulatedEntity],
    snapshot: Document,
    *,
    today: date | None = None,
) -> CRReport:
    report = CRReport(entities=len(entities))
    recent_since = (today or date.today()) - timedelta(days=RECENT_DAYS)
    index = _Index(session)
    book = EntityBook(session)
    existing: dict[tuple[str, str], Signal] = {
        (s.rn or "", s.permit_number or ""): s
        for s in session.scalars(select(Signal).where(Signal.source == SOURCE))
    }
    known_air = {
        s.permit_number
        for s in session.scalars(
            select(Signal).where(Signal.type == SignalType.AIR_PERMIT, Signal.source != SOURCE)
        )
    }
    for ent in entities:
        tracked = ent.rn in index.by_rn
        reason = "site already has this RN" if tracked else data_center_reason(ent, _known_company)
        if reason is None:
            continue
        wanted = [p for p in ent.programs if p.program in SIGNAL_TYPE]
        wanted = [p for p in wanted if not (p.program == "AIRNSR" and p.number in known_air)]
        report.data_centers += 1
        site, how, note, review = _match(ent, index)
        if site is None:
            if not wanted:
                continue  # a fuel tank or septic registration alone doesn't make a site
            site = Site(name=ent.name, county=ent.county, tceq_rns=[ent.rn])
            site.set_status(SiteStatus.FILED, note=f"TCEQ Central Registry {ent.rn} ({reason})")
            session.add(site)
            session.flush()
            index.add(site)
            confidence = None
        else:
            confidence = None if how == "rn" else Confidence.MEDIUM
        first_time = ent.rn not in (site.tceq_rns or [])
        if first_time:
            site.tceq_rns = [*(site.tceq_rns or []), ent.rn]
            index.add(site)
            if ent.name != site.name and ent.name not in site.aliases:
                site.aliases = [*site.aliases, ent.name]
        if site.county is None and ent.county:
            site.county = ent.county
        if how != "rn":
            match = CRMatch(ent, site, how, note)
            report.matched.append(match)
        for other in review:
            if add_link(
                session,
                from_type=RecordType.SITE,
                from_id=site.id,
                to_type=RecordType.SITE,
                to_id=other.id,
                link_type=LinkType.SAME_SITE,
                method=LinkMethod.NAME_MATCH,
                confidence=Confidence.LOW,
                evidence_url=evidence_url(ent),
                note=f"TCEQ {ent.rn} '{ent.name}' has principal '{ent.principal}', "
                f"also tied to '{other.name}'",
            ):
                report.review.append(f"{ent.rn} {ent.name}  ?=  {other.name}")

        for program in wanted:
            added = _signal(session, report, existing, site, ent, program, snapshot, reason)
            if added is not None and confidence is not None:
                add_link(
                    session,
                    from_type=RecordType.SIGNAL,
                    from_id=added.id,
                    to_type=RecordType.SITE,
                    to_id=site.id,
                    link_type=LinkType.SAME_SITE,
                    method=LinkMethod.NAME_MATCH,
                    confidence=confidence,
                    evidence_url=evidence_url(ent),
                    note=note,
                )
        # Construction authorizations often stay open for years after a building is done,
        # so only a recent one says dirt is moving now.
        active = [
            p
            for p in ent.programs
            if p.is_construction_stormwater
            and p.status == "ACTIVE"
            and p.since is not None
            and p.since >= recent_since
        ]
        if active and site.status in BEFORE_CONSTRUCTION:
            site.set_status(
                SiteStatus.UNDER_CONSTRUCTION,
                note=f"active construction stormwater {active[0].number} (TCEQ {ent.rn})",
            )
            report.to_construction.append(site.name)
        if ent.principal:
            link_party(
                session,
                book,
                ent.principal,
                site,
                LinkType.FILED_BY,
                confidence or Confidence.HIGH,
                evidence_url=evidence_url(ent),
                note=f"principal on TCEQ Central Registry {ent.rn} ({ent.name})",
            )
    session.flush()
    return report


def _signal(
    session: Session,
    report: CRReport,
    existing: dict[tuple[str, str], Signal],
    site: Site,
    ent: RegulatedEntity,
    program: ProgramId,
    snapshot: Document,
    reason: str,
) -> Signal | None:
    """Add a signal for a new authorization (returned), or a new extraction if it changed."""
    row = {
        "rn": ent.rn,
        "program": program.program,
        "number": program.number,
        "status": program.status,
        "since": program.since.isoformat() if program.since else None,
        "construction": program.is_construction_stormwater,
        "site_name": ent.name,
        "county": ent.county,
        "city": ent.city,
        "address": ent.address,
        "industry": ent.industry,
        "principal": ent.principal,
    }
    fields = {
        **row,
        "why_data_center": reason,
        "url": evidence_url(ent),
        "snapshot": snapshot.sha256,
    }
    signal = existing.get((ent.rn, program.number))
    if signal is not None:
        last = signal.extractions[-1].fields
        if {k: last.get(k) for k in row} != row:
            report.changed += 1
            _extract(session, signal, fields)
        return None
    kind = (
        "construction stormwater"
        if program.is_construction_stormwater
        else (PROGRAM_LABEL[program.program].lower())
    )
    signal = Signal(
        site=site,
        type=SIGNAL_TYPE[program.program],
        source=SOURCE,
        title=f"TCEQ {kind} {program.number} ({ent.name})",
        filing_date=program.since,
        permit_number=program.number,
        rn=ent.rn,
        raw_text=json.dumps(row),
    )
    session.add(signal)
    _extract(session, signal, fields)
    session.flush()
    existing[(ent.rn, program.number)] = signal
    report.new_signals += 1
    return signal


def _extract(session: Session, signal: Signal, fields: dict) -> None:
    session.add(
        Extraction(
            signal=signal, extractor=EXTRACTOR, extractor_version=EXTRACTOR_VERSION, fields=fields
        )
    )
