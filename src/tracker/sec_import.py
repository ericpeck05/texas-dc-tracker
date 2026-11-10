"""SEC filings that name a tracked site, as sec_mention signals.

A hit names a site by its exact, distinctive name, so it goes to that site at medium
confidence (the filer may be the owner, a tenant, a lender, or a supplier; the signal says
the site is real and public money is near it, not who owns it). A phrase that is the name of
more than one site goes to none of them, with low-confidence links for review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.collectors.sec.edgar import SOURCE, Query, search_phrase
from tracker.db.models import (
    Confidence,
    Document,
    Extraction,
    LinkMethod,
    LinkType,
    RecordType,
    Signal,
    SignalType,
    Site,
)
from tracker.entities import add_link

EXTRACTOR = "sec_edgar_fulltext"
EXTRACTOR_VERSION = "0.1.0"


@dataclass
class SecReport:
    hits: int = 0
    searches: int = 0  # site names searched (0 when importing a saved snapshot)
    new_signals: int = 0
    matched: list[tuple[str, str, str]] = field(default_factory=list)  # phrase, company, site
    review: list[str] = field(default_factory=list)


def site_queries(session: Session) -> list[Query]:
    """One query per distinctive site name, for sites with at least one signal."""
    seen: dict[str, Query] = {}
    with_signals = set(session.scalars(select(Signal.site_id).where(Signal.site_id.is_not(None))))
    for site in session.scalars(select(Site).order_by(Site.id)):
        if site.id not in with_signals:
            continue
        for name in [site.name, *(site.aliases or [])]:
            phrase = search_phrase(name)
            if phrase and phrase.lower() not in seen:
                seen[phrase.lower()] = Query(phrase, [p for p in (site.county, site.city) if p])
    return list(seen.values())


def _sites_named(session: Session, phrase: str) -> list[Site]:
    key = phrase.lower()
    return [
        s
        for s in session.scalars(select(Site).order_by(Site.id))
        if any((search_phrase(n) or "").lower() == key for n in [s.name, *(s.aliases or [])])
    ]


def import_sec(session: Session, hits: list[dict], snapshot: Document) -> SecReport:
    report = SecReport(hits=len(hits))
    existing = {
        (s.permit_number, (s.extractions[-1].fields or {}).get("phrase") if s.extractions else None)
        for s in session.scalars(select(Signal).where(Signal.source == SOURCE))
    }
    for h in hits:
        if (h["adsh"], h["phrase"]) in existing:
            continue
        sites = _sites_named(session, h["phrase"])
        site = sites[0] if len(sites) == 1 else None
        signal = Signal(
            site=site,
            type=SignalType.SEC_MENTION,
            source=SOURCE,
            title=f"{h['company']} {h['form']}: names '{h['phrase']}'",
            filing_date=_date(h.get("file_date")),
            permit_number=h["adsh"],
            raw_text=json.dumps(h),
        )
        session.add(signal)
        session.add(
            Extraction(
                signal=signal,
                extractor=EXTRACTOR,
                extractor_version=EXTRACTOR_VERSION,
                fields={**h, "snapshot": snapshot.sha256},
            )
        )
        session.flush()
        existing.add((h["adsh"], h["phrase"]))
        report.new_signals += 1
        for s in sites:
            add_link(
                session,
                from_type=RecordType.SIGNAL,
                from_id=signal.id,
                to_type=RecordType.SITE,
                to_id=s.id,
                link_type=LinkType.SAME_SITE,
                method=LinkMethod.NAME_MATCH,
                confidence=Confidence.MEDIUM if site else Confidence.LOW,
                evidence_url=h["url"],
                note=f"{h['company']} {h['form']} names '{h['phrase']}'"
                + ("" if site else ", the name of more than one site"),
            )
            if site is None:
                report.review.append(f"{h['adsh']} '{h['phrase']}'  ?=  {s.name}")
        if site is not None:
            report.matched.append((h["phrase"], h["company"], site.name))
    return report


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
