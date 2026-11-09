"""Turn a raw document into Document -> Signal -> Extraction -> Equipment rows.

Site matching here is only the high-confidence pass: exact TCEQ RN number.
Fuzzy matching and owner unmasking belong to the linking engine (Phase 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import (
    Document,
    Equipment,
    Extraction,
    Signal,
    SignalType,
    Site,
    SiteStatus,
)
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


def ingest_tceq_air(
    session: Session,
    content: bytes,
    *,
    raw_dir: Path,
    url: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    source: str = "tceq_air",
) -> IngestResult:
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
        raw_text=text,
    )
    session.add(signal)

    site = None
    if parsed.rn:
        site = _find_site_by_rn(session, parsed.rn)
        if site is None:
            site = Site(
                name=parsed.applicant or f"Unnamed site {parsed.rn}",
                county=parsed.county,
                tceq_rns=[parsed.rn],
            )
            site.set_status(SiteStatus.FILED, signal=signal, note="created from TCEQ air permit")
            session.add(site)
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
            )
        )
    session.flush()
    return IngestResult(doc, signal, created=True, site=site)
