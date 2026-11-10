"""Feed: filings newest first, each tagged by signal type and linked to its site.

Answers "what changed since I last looked?". Ordered by filing date (the date on the
document), since a rebuild resets when each filing was first stored. Equipment shown is what
that one filing lists, before netting against the site's other filings.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import Signal, Site
from tracker.views.sites import SOURCE_LABEL, _signal_row, _site_companies, owner_chain
from tracker.views.ticker import _kind, _power


def _filing_equipment(sig: Signal) -> dict:
    out = {"generators": 0, "turbines": 0, "mw": 0.0}
    for row in sig.equipment:
        kind = _kind(row.category)
        if kind is None or not row.count:
            continue
        out[kind] += row.count
        out["mw"] += _power(str(row.category), row.count, row.unit_size, row.unit)
    out["mw"] = round(out["mw"], 1)
    return out


def feed(
    session: Session,
    *,
    days: int | None = 30,
    signal_type: str | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    query = select(Signal).where(Signal.filing_date.is_not(None))
    if days:
        query = query.where(Signal.filing_date >= today - timedelta(days=days))
    if signal_type:
        query = query.where(Signal.type == signal_type)
    signals = session.scalars(query.order_by(Signal.filing_date.desc(), Signal.id.desc())).all()
    companies: dict[int, list[dict]] = {}
    items = []
    for sig in signals:
        site: Site | None = sig.site
        if site is not None and site.id not in companies:
            chain = _site_companies(owner_chain(session, site))
            companies[site.id] = [
                {"company": c["company"], "ticker": c["ticker"], "confidence": c["confidence"]}
                for c in chain
                if c["confidence"] != "low"
            ]
        row = _signal_row(session, sig)
        items.append(
            {
                "id": sig.id,
                "date": row["date"],
                "type": row["type"],
                "title": row["title"],
                "url": row["url"],
                "permit": row["permit"],
                "status": row["status"],
                "source_label": SOURCE_LABEL.get(sig.source, sig.source),
                "site": (
                    {
                        "id": site.id,
                        "name": site.name,
                        "county": site.county,
                        "status": str(site.status),
                    }
                    if site
                    else None
                ),
                "companies": companies.get(site.id, []) if site else [],
                "equipment": _filing_equipment(sig),
                "revised": len(sig.extractions) > 1,  # re-read after the source changed
            }
        )
    latest = session.scalar(select(Signal.filing_date).order_by(Signal.filing_date.desc()))
    return {
        "days": days,
        "today": today.isoformat(),
        "latest_filing": latest.isoformat() if latest else None,
        "items": items,
    }
