"""Turn a raw document into Document -> Signal -> Extraction -> Equipment rows.

Site matching here is only the exact passes: TCEQ RN number, then permit number (a later
filing under the same permit). A document with neither match gets a new Site.
The applicant becomes an Entity tied to the site (FILED_BY, high confidence: the filing
names it for this site), and to a known company if its name is one (tracker.entities).
Fuzzy matching (name, address, coordinates), merging duplicate sites, and owner unmasking
belong to the linking engine (Phase 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import (
    Confidence,
    Document,
    Equipment,
    Extraction,
    LinkType,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
from tracker.entities import EntityBook, link_party
from tracker.extract.tceq_air import EXTRACTOR_NAME, EXTRACTOR_VERSION, extract_air_permit
from tracker.parsers.pdf import document_to_text
from tracker.storage import StoredFile, guess_extension, save_raw


@dataclass
class IngestResult:
    document: Document
    signal: Signal
    created: bool  # False if this exact file was already ingested
    site: Site | None


def _find_site_by_rn(session: Session, rn: str) -> Site | None:
    for site in session.scalars(select(Site)):
        if rn in (site.tceq_rns or []):
            return site
    return None


def _find_site_by_permit(session: Session, permit_number: str) -> Site | None:
    return session.scalar(
        select(Site)
        .join(Signal, Signal.site_id == Site.id)
        .where(Signal.permit_number == permit_number)
        .limit(1)
    )


def _find_site_referencing_permit(session: Session, permit_number: str) -> Site | None:
    """A site whose earlier filing names this permit (a Title V permit listing its NSR
    authorizations, or a filing saying this permit is at the same site)."""
    for ex in session.scalars(select(Extraction).join(Signal).where(Signal.site_id.is_not(None))):
        refs = [a.get("number") for a in ex.fields.get("nsr_authorizations", [])]
        refs += ex.fields.get("same_site_permits", [])
        if permit_number in refs:
            return ex.signal.site
    return None


def _match_or_create_site(
    session: Session, parsed, signal: Signal, site_name_hint: str | None = None
) -> Site | None:
    site = None
    if parsed.rn:
        site = _find_site_by_rn(session, parsed.rn)
    if site is None:
        # Same permit, or a permit this filing explicitly names (a Title V permit lists the
        # NSR authorizations it covers), or an earlier filing that named this one.
        referenced = [a["number"] for a in parsed.nsr_authorizations]
        referenced += parsed.same_site_permits
        for number in filter(None, [parsed.permit_number, *referenced]):
            if site := _find_site_by_permit(session, number):
                break
        if site is None and parsed.permit_number:
            site = _find_site_referencing_permit(session, parsed.permit_number)
    if site is not None:
        if parsed.rn and parsed.rn not in site.tceq_rns:
            site.tceq_rns = [*site.tceq_rns, parsed.rn]
        for alias in (parsed.site_name, parsed.applicant):
            if alias and alias != site.name and alias not in site.aliases:
                site.aliases = [*site.aliases, alias]
        return site

    key = parsed.rn or parsed.permit_number
    if key is None:
        return None  # nothing stable to key a site on; leave it for the linking engine
    name = parsed.site_name or site_name_hint or parsed.applicant or f"Unnamed site {key}"
    aliases = [a for a in (parsed.site_name, parsed.applicant) if a and a != name]
    site = Site(
        name=name,
        aliases=aliases,
        county=parsed.county,
        tceq_rns=[parsed.rn] if parsed.rn else [],
    )
    how = f"RN {parsed.rn}" if parsed.rn else f"permit {parsed.permit_number}, no RN in filing"
    site.set_status(SiteStatus.FILED, signal=signal, note=f"created from TCEQ air permit ({how})")
    session.add(site)
    return site


def ingest_tceq_air(
    session: Session,
    content: bytes,
    *,
    raw_dir: Path,
    url: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    source: str = "tceq_air",
    site_name_hint: str | None = None,
) -> IngestResult:
    """site_name_hint: the site name from where the document was found (a Records Online
    search result), used only when the document itself names no site."""
    ext = guess_extension(content_type, url or filename)
    stored: StoredFile = save_raw(
        content, source=source, raw_dir=raw_dir, ext=ext, content_type=content_type
    )

    doc = session.scalar(select(Document).where(Document.sha256 == stored.sha256))
    if doc is not None:
        signal = session.scalar(
            select(Signal).where(Signal.document_id == doc.id, Signal.type == SignalType.AIR_PERMIT)
        )
        if signal is not None:
            return IngestResult(doc, signal, created=False, site=signal.site)
    else:
        doc = Document(
            source=source,
            url=url,
            sha256=stored.sha256,
            local_path=str(stored.path),
            content_type=content_type,
            size_bytes=stored.size,
            listed_site_name=site_name_hint,
        )
        session.add(doc)

    text, parse_method = document_to_text(stored.path)
    parsed = extract_air_permit(text)

    signal = Signal(
        document=doc,
        type=SignalType.AIR_PERMIT,
        source=source,
        title=filename or url,
        permit_number=parsed.permit_number,
        rn=parsed.rn,
        filing_date=date.fromisoformat(parsed.document_date) if parsed.document_date else None,
        raw_text=text,
    )
    session.add(signal)

    site = _match_or_create_site(session, parsed, signal, site_name_hint)
    signal.site = site

    fields = parsed.to_fields()
    fields["parse_method"] = parse_method
    extraction = Extraction(
        signal=signal,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        fields=fields,
    )
    session.add(extraction)

    for m in parsed.equipment:
        session.add(
            Equipment(
                signal=signal,
                site=site,
                extraction=extraction,
                category=m.category,
                count=m.count,
                unit_size=m.unit_size,
                unit=m.unit,
                fuel=m.fuel,
                manufacturer=m.manufacturer,
                model=m.model,
                page=m.page,
                snippet=m.snippet,
                method=m.method,
                role=m.role,
                epns=m.epns,
            )
        )
    session.flush()
    if parsed.applicant:
        # The applicant is named on the filing for this site: a direct, high-confidence tie.
        kind = (parsed.document_kind or "filing").replace("_", " ")
        status = ", withdrawn" if parsed.withdrawn else ""
        link_party(
            session,
            EntityBook(session),
            parsed.applicant,
            site,
            LinkType.FILED_BY,
            Confidence.HIGH,
            evidence_url=url,
            note=f"applicant on TCEQ permit {parsed.permit_number or '?'} ({kind}{status})",
        )
        session.flush()
    return IngestResult(doc, signal, created=True, site=site)
