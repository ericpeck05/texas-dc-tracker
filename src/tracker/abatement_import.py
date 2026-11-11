"""Load Chapter 312 abatement agreements for data centers as APPROVAL signals.

Only agreements that look like data centers are kept (see
`abatements.data_center_reason`). Each becomes an APPROVAL signal keyed by its Record ID,
and its organization owners become entities that OWN the site (high confidence: a
government filing names them as the property owner).

Matching an agreement to a site, in order, stopping at the first that applies:

1. Same agreement: the Record ID (or the older record a modification replaces) is already
   on a site.
2. Owner match (medium): an owner is the same legal entity as one already tied to exactly
   one site ("Sharka LLC" owns Sharka Data Center in the registry).
3. Owner and place (medium): the owner is on several sites, and exactly one of them is in
   the agreement's county or has a place from the agreement in its name ("Houston City"
   picks "Cyrusone LLC - Houston West III" over "San Antonio Data Center II").

If an owner is on several sites and no place decides it, the signal is left without a site
and each candidate gets a low-confidence SAME_SITE link for review. With no owner match
the agreement gets its own site, status approved.

A match also fills in the county of a site that had none (registry sites have no address).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.comptroller.abatements import (
    SOURCE,
    Abatement,
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

EXTRACTOR = "comptroller_ch312_csv"
EXTRACTOR_VERSION = "0.1.0"
EARLY = {SiteStatus.RUMORED, SiteStatus.FILED}


@dataclass
class AbatementMatch:
    abatement: Abatement
    site: Site | None
    how: str  # record, owner, owner+place, review, new
    note: str


@dataclass
class AbatementReport:
    agreements: int = 0  # every agreement in the file
    data_centers: int = 0
    new: list[AbatementMatch] = field(default_factory=list)
    changed: list[Abatement] = field(default_factory=list)
    unchanged: int = 0
    review: list[str] = field(default_factory=list)


def _site_words(site: Site) -> set[str]:
    words: set[str] = set()
    for name in (site.name, *site.aliases):
        words |= set(re.findall(r"[a-z]+", name.lower()))
    return words


def _entity_sites(session: Session) -> dict[str, set[int]]:
    """Entity key -> ids of sites it is tied to (any role, any source)."""
    names = {e.id: e.legal_name for e in session.scalars(select(Entity))}
    out: dict[str, set[int]] = {}
    for link in session.scalars(
        select(Link).where(Link.from_type == RecordType.ENTITY, Link.to_type == RecordType.SITE)
    ):
        out.setdefault(entity_key(names[link.from_id]), set()).add(link.to_id)
    return out


def _match(
    session: Session, ab: Abatement, by_entity: dict[str, set[int]]
) -> tuple[Site | None, str, str, list[Site]]:
    candidates: dict[int, str] = {}
    for owner in ab.owners:
        for site_id in by_entity.get(entity_key(owner), ()):
            candidates.setdefault(site_id, owner)
    if not candidates:
        return None, "new", "", []
    if len(candidates) == 1:
        site_id, owner = next(iter(candidates.items()))
        return session.get(Site, site_id), "owner", f"owner '{owner}' is tied to this site", []
    sites = [session.get(Site, i) for i in sorted(candidates)]
    places = ab.place_words()
    county = (ab.county or "").lower()
    fits = [
        s for s in sites if (s.county and s.county.lower() == county) or (_site_words(s) & places)
    ]
    # A site placed in another county can't be this one.
    fits = [s for s in fits if not (s.county and county and s.county.lower() != county)]
    if len(fits) == 1:
        site = fits[0]
        owner = candidates[site.id]
        where = site.county if site.county else ", ".join(sorted(_site_words(site) & places))
        return site, "owner+place", f"owner '{owner}' is tied to this site, in {where}", []
    return None, "review", "", fits or sites


def _new_site_name(ab: Abatement) -> str:
    """'City of Allen Compass Datacenters Reinvestment Zone #33' -> 'Compass Datacenters
    (Collin County)'; zones named only by number or tract use the owner instead."""
    zone = re.sub(r"^(?:City|County|Town) of \w+\s+", "", ab.zone, flags=re.I)
    zone = re.sub(r"\s*\bReinvestment Zone\b.*$", "", zone, flags=re.I).strip()
    if re.search(r"data\s?cent|project|campus", zone, re.IGNORECASE):
        base = zone
    else:
        base = re.sub(r",?\s+(?:LLC|Inc\.?|L\.?P\.?|Corp\.?)$", "", ab.owners[0], flags=re.I)
    return f"{base} ({ab.county} County)" if ab.county else base


def import_abatements(
    session: Session, abatements: list[Abatement], snapshot: Document
) -> AbatementReport:
    report = AbatementReport(agreements=len(abatements))
    book = EntityBook(session)
    by_entity = _entity_sites(session)
    existing = {
        s.permit_number: s for s in session.scalars(select(Signal).where(Signal.source == SOURCE))
    }
    for ab in abatements:
        reason = data_center_reason(ab, set(by_entity))
        if reason is None:
            continue
        report.data_centers += 1
        row = ab.as_row()
        fields = {**row, "why_data_center": reason, "url": ab.url, "snapshot": snapshot.sha256}
        signal = existing.get(ab.record_id)
        if signal is not None:
            last = signal.extractions[-1].fields
            if {k: last.get(k) for k in row} == row:
                report.unchanged += 1
                continue
            report.changed.append(ab)
            _extract(session, signal, fields)
            site = signal.site
            confidence = _signal_confidence(session, signal)
        else:
            site, how, note, weaker = None, "new", "", []
            inherited = None
            previous = existing.get(ab.previous_record_id or "")
            if previous is not None and previous.site is not None:
                site, how = previous.site, "record"
                note = f"modifies agreement {ab.previous_record_id}"
                inherited = _signal_confidence(session, previous)
            else:
                site, how, note, weaker = _match(session, ab, by_entity)
            created = False
            if site is None and how == "new":
                site = Site(name=_new_site_name(ab), county=ab.county)
                session.add(site)
                created = True
            signal = Signal(
                site=site,
                type=SignalType.APPROVAL,
                source=SOURCE,
                title=f"Tax abatement: {ab.zone} ({ab.lead_unit})",
                filing_date=ab.executed,
                permit_number=ab.record_id,
                raw_text=json.dumps(row),
            )
            session.add(signal)
            _extract(session, signal, fields)
            session.flush()
            existing[ab.record_id] = signal
            confidence = None if created else Confidence.MEDIUM
            if how == "record":
                confidence = inherited
            if site is not None and confidence is not None:
                add_link(
                    session,
                    from_type=RecordType.SIGNAL,
                    from_id=signal.id,
                    to_type=RecordType.SITE,
                    to_id=site.id,
                    link_type=LinkType.SAME_SITE,
                    method=LinkMethod.NAME_MATCH,
                    confidence=confidence,
                    evidence_url=ab.url,
                    note=note,
                )
            for other in weaker:
                add_link(
                    session,
                    from_type=RecordType.SIGNAL,
                    from_id=signal.id,
                    to_type=RecordType.SITE,
                    to_id=other.id,
                    link_type=LinkType.SAME_SITE,
                    method=LinkMethod.NAME_MATCH,
                    confidence=Confidence.LOW,
                    evidence_url=ab.url,
                    note=f"abatement {ab.record_id} ({ab.lead_unit}) names an owner of "
                    f"'{other.name}', but so do other sites",
                )
                report.review.append(f"{ab.record_id} {ab.zone}  ?=  {other.name}")
            report.new.append(AbatementMatch(ab, site, how, note))
            if site is not None:
                if site.county is None and ab.county:
                    site.county = ab.county
                if site.status is None or site.status in EARLY:
                    site.set_status(
                        SiteStatus.APPROVED,
                        signal=signal,
                        note=f"Chapter 312 tax abatement {ab.record_id} with {ab.lead_unit}",
                    )
        if site is None:
            continue
        for owner in ab.owners:
            entity = link_party(
                session,
                book,
                owner,
                site,
                LinkType.OWNS,
                confidence or Confidence.HIGH,
                evidence_url=ab.url,
                note=f"property owner on Chapter 312 abatement {ab.record_id} "
                f"({ab.lead_unit}, {ab.status.lower()})",
            )
            if entity is not None:
                by_entity.setdefault(entity_key(owner), set()).add(site.id)
    session.flush()
    return report


def _extract(session: Session, signal: Signal, fields: dict) -> None:
    session.add(
        Extraction(
            signal=signal, extractor=EXTRACTOR, extractor_version=EXTRACTOR_VERSION, fields=fields
        )
    )


def _signal_confidence(session: Session, signal: Signal) -> Confidence | None:
    if signal.site_id is None:
        return None
    return session.scalar(
        select(Link.confidence).where(
            Link.from_type == RecordType.SIGNAL,
            Link.from_id == signal.id,
            Link.to_id == signal.site_id,
            Link.link_type == LinkType.SAME_SITE,
        )
    )
