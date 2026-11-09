"""Site list and site page data.

The site page answers "what is this project and who is behind it?": a timeline of every
signal, the equipment counted from air permits, and the owner chain from each legal entity
on a filing up to a parent company, with the confidence and evidence of every step.

Only organizations are entities, and an entity's officers and registered agent are never
returned (they can be private individuals).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import (
    Company,
    Confidence,
    Entity,
    Link,
    LinkType,
    RecordType,
    Signal,
    Site,
)
from tracker.entities import RANK
from tracker.rollup import site_inventory
from tracker.views.ticker import ROLE_LABEL, _doc_ref, _kind, _power, site_power

SOURCE_LABEL = {
    "tceq_air": "TCEQ air permit document",
    "comptroller_dc_registry": "Comptroller data center registry",
    "comptroller_ch312": "Comptroller Chapter 312 abatements",
    "tceq_central_registry": "TCEQ Central Registry",
}


def _step(link: Link) -> dict:
    return {
        "confidence": str(link.confidence),
        "method": str(link.method),
        "evidence_url": link.evidence_url,
        "note": link.note,
    }


def _ticker(company: Company) -> tuple[str | None, str | None]:
    if not company.tickers:
        return None, None
    return company.tickers[0].symbol, company.tickers[0].exchange


def _links(session: Session, **where) -> list[Link]:
    query = select(Link)
    for column, value in where.items():
        query = query.where(getattr(Link, column) == value)
    return session.scalars(query.order_by(Link.id)).all()


def _companies_of(session: Session, entity_id: int) -> list[dict]:
    out = []
    for cl in _links(
        session, to_type=RecordType.ENTITY, to_id=entity_id, from_type=RecordType.COMPANY
    ):
        company = session.get(Company, cl.from_id)
        symbol, exchange = _ticker(company)
        out.append(
            {
                "company": company.name,
                "role": str(company.role),
                "ticker": symbol,
                "exchange": exchange,
                **_step(cl),
            }
        )
    return sorted(out, key=lambda c: -RANK[Confidence(c["confidence"])])


def _affiliates(session: Session, entity_id: int) -> list[dict]:
    out = []
    for link in _links(
        session, from_type=RecordType.ENTITY, from_id=entity_id, link_type=LinkType.AFFILIATE_OF
    ):
        if link.to_type == RecordType.ENTITY:
            other = session.get(Entity, link.to_id)
            out.append({"entity": other.legal_name, **_step(link)})
    for link in _links(
        session, to_type=RecordType.ENTITY, to_id=entity_id, link_type=LinkType.AFFILIATE_OF
    ):
        if link.from_type == RecordType.ENTITY:
            other = session.get(Entity, link.from_id)
            out.append({"entity": other.legal_name, **_step(link)})
    return out


def owner_chain(session: Session, site: Site) -> list[dict]:
    """Each entity on the site, its role(s), and the companies behind it."""
    by_entity: dict[int, list[Link]] = defaultdict(list)
    for link in _links(
        session, to_type=RecordType.SITE, to_id=site.id, from_type=RecordType.ENTITY
    ):
        by_entity[link.from_id].append(link)
    out = []
    for entity_id, links in by_entity.items():
        entity = session.get(Entity, entity_id)
        roles = {}
        for link in links:  # strongest link per role
            role = ROLE_LABEL.get(str(link.link_type), str(link.link_type))
            if (
                role not in roles
                or RANK[link.confidence] > RANK[Confidence(roles[role]["confidence"])]
            ):
                roles[role] = {"role": role, **_step(link)}
        companies = _companies_of(session, entity_id)
        site_conf = max(
            (r["confidence"] for r in roles.values()), key=lambda c: RANK[Confidence(c)]
        )
        best = None
        if companies:
            top = companies[0]
            best = min(site_conf, top["confidence"], key=lambda c: RANK[Confidence(c)])
        out.append(
            {
                "entity": entity.legal_name,
                "entity_type": entity.entity_type,
                "roles": sorted(roles.values(), key=lambda r: r["role"]),
                "companies": companies,
                "affiliates": _affiliates(session, entity_id),
                "chain_confidence": best,  # weaker of entity->site and company->entity
            }
        )
    out.sort(
        key=lambda e: (
            -RANK[Confidence(e["chain_confidence"])] if e["chain_confidence"] else 1,
            e["entity"],
        )
    )
    return out


def _site_companies(chain: list[dict]) -> list[dict]:
    """Companies behind the site, one row per company, strongest chain kept."""
    best: dict[str, dict] = {}
    for e in chain:
        for c in e["companies"]:
            conf = min(
                c["confidence"],
                max((r["confidence"] for r in e["roles"]), key=lambda x: RANK[Confidence(x)]),
                key=lambda x: RANK[Confidence(x)],
            )
            roles = [r["role"] for r in e["roles"]]
            row = best.get(c["company"])
            if row is None or RANK[Confidence(conf)] > RANK[Confidence(row["confidence"])]:
                best[c["company"]] = {
                    "company": c["company"],
                    "ticker": c["ticker"],
                    "confidence": conf,
                    "roles": roles,
                }
            elif RANK[Confidence(conf)] == RANK[Confidence(row["confidence"])]:
                row["roles"] = sorted(set(row["roles"]) | set(roles))
    return sorted(best.values(), key=lambda r: (-RANK[Confidence(r["confidence"])], r["company"]))


def _signal_row(session: Session, sig: Signal) -> dict:
    match = session.scalar(
        select(Link).where(
            Link.from_type == RecordType.SIGNAL,
            Link.from_id == sig.id,
            Link.to_type == RecordType.SITE,
            Link.to_id == sig.site_id,
            Link.link_type == LinkType.SAME_SITE,
        )
    )
    fields = sig.extractions[-1].fields if sig.extractions else {}
    ref = _doc_ref(sig)
    if not ref["title"] or ref["title"].startswith("http"):
        kind = (fields.get("document_kind") or "document").replace("_", " ")
        number = f"permit {sig.permit_number}" if sig.permit_number else "unnumbered permit"
        ref["title"] = f"TCEQ {number}: {kind}" + (
            " (withdrawn)" if fields.get("withdrawn") else ""
        )
    return {
        **ref,
        "id": sig.id,
        "source": sig.source,
        "source_label": SOURCE_LABEL.get(sig.source, sig.source),
        "rn": sig.rn,
        "status": fields.get("status") or fields.get("abatement_status"),
        "equipment_rows": len(sig.equipment),
        # How this signal was tied to the site: None means it created the site, or carries
        # the site's own ID (RN or permit number).
        "match": _step(match) if match else None,
    }


def equipment(session: Session, site: Site) -> list[dict]:
    """Counted units per permit (each unit once, newest filing wins), with page citations."""
    inv = site_inventory(session, site)
    out = []
    for permit in inv.permits:
        rows = []
        for row in permit.rows:
            sign = permit.sign(row)
            if not sign:
                continue
            size, unit = permit.size(row)
            kind = _kind(row.category)
            rows.append(
                {
                    "category": str(row.category),
                    "kind": kind,
                    "count": sign * row.count,
                    "manufacturer": row.manufacturer,
                    "model": row.model,
                    "fuel": row.fuel,
                    "units": row.epns,
                    "unit_size": size,
                    "unit": unit,
                    "mw": round(sign * _power(str(row.category), row.count, size, unit), 2)
                    if kind
                    else None,
                    "page": row.page,
                    "snippet": row.snippet,
                }
            )
        if rows:
            out.append(
                {
                    **_doc_ref(permit.signal, permit.permit_number),
                    "other_documents": permit.other_documents,
                    "rows": rows,
                }
            )
    return out


def _review(session: Session, site: Site) -> list[dict]:
    """Low-confidence 'same site?' links touching this site, for a human to settle."""
    out = []
    for link in _links(session, link_type=LinkType.SAME_SITE, confidence=Confidence.LOW):
        other_id = None
        if link.from_type == RecordType.SITE and link.from_id == site.id:
            other_id = link.to_id if link.to_type == RecordType.SITE else None
        elif link.to_type == RecordType.SITE and link.to_id == site.id:
            if link.from_type == RecordType.SITE:
                other_id = link.from_id
            elif link.from_type == RecordType.SIGNAL:
                sig = session.get(Signal, link.from_id)
                out.append({"signal": _doc_ref(sig), "site": None, **_step(link)})
                continue
        if other_id is not None:
            other = session.get(Site, other_id)
            out.append(
                {"site": {"id": other.id, "name": other.name}, "signal": None, **_step(link)}
            )
    return out


def site_view(session: Session, site_id: int) -> dict | None:
    site = session.get(Site, site_id)
    if site is None:
        return None
    signals = sorted(
        session.scalars(select(Signal).where(Signal.site_id == site.id)).all(),
        key=lambda s: (s.filing_date or date.min, s.id),
        reverse=True,
    )
    chain = owner_chain(session, site)
    return {
        "id": site.id,
        "name": site.name,
        "aliases": site.aliases or [],
        "county": site.county,
        "city": site.city,
        "status": str(site.status),
        "status_history": [
            {
                "from": str(ch.old_status) if ch.old_status else None,
                "to": str(ch.new_status),
                "at": ch.changed_at.date().isoformat() if ch.changed_at else None,
                "note": ch.note,
            }
            for ch in site.status_changes
        ],
        "tceq_rns": site.tceq_rns or [],
        "signal_types": sorted({str(s.type) for s in signals}),
        "sources": sorted({s.source for s in signals}),
        "signals": [_signal_row(session, s) for s in signals],
        "power": site_power(session, site),
        "equipment": equipment(session, site),
        "owner_chain": chain,
        "companies": _site_companies(chain),
        "review": _review(session, site),
    }


def site_list(session: Session) -> list[dict]:
    """Every site with the columns the site table sorts and filters on."""
    rows = []
    for site in session.scalars(select(Site).order_by(Site.name)):
        signals = session.scalars(select(Signal).where(Signal.site_id == site.id)).all()
        if not signals:
            continue
        latest = max(signals, key=lambda s: (s.filing_date or date.min, s.id))
        power = site_power(session, site)
        companies = _site_companies(owner_chain(session, site))
        shown = [c for c in companies if c["confidence"] != "low"]
        rows.append(
            {
                "id": site.id,
                "name": site.name,
                "county": site.county,
                "status": str(site.status),
                "signal_types": sorted({str(s.type) for s in signals}),
                "sources": len({s.source for s in signals}),
                "signal_count": len(signals),
                "latest_date": latest.filing_date.isoformat() if latest.filing_date else None,
                "generators": power["generators"],
                "turbines": power["turbines"],
                "mw": power["mw"],
                "unsized": power["unsized"],
                "companies": [
                    {"company": c["company"], "ticker": c["ticker"], "confidence": c["confidence"]}
                    for c in shown
                ],
            }
        )
    return rows
