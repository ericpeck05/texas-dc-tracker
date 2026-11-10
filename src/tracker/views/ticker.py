"""Ticker page data: every site tied to one stock ticker, and the equipment it supplies.

Two sides, as in the build plan:

- Demand: sites whose owner, tenant, operator, or permit holder belongs to a company with
  this ticker. Each tie carries its confidence and the chain of evidence (entity named on a
  filing -> company behind the entity). Low-confidence ties are listed apart as leads,
  never counted in totals.
- Supply: generators and turbines made by a company with this ticker (Caterpillar, Solar
  Turbines and Perkins all roll up to CAT), counted once per unit as in tracker.rollup.

Everything returned is plain dicts and lists, ready for JSON. No person's name is ever
included: only organizations become entities (tracker.entities).
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
    EquipmentCategory,
    Link,
    RecordType,
    Signal,
    Site,
    Ticker,
)
from tracker.entities import RANK, not_rejected
from tracker.reference.suppliers import SUPPLIERS, Supplier
from tracker.rollup import site_inventory

GENERATORS = {
    EquipmentCategory.DIESEL_GENERATOR,
    EquipmentCategory.GAS_GENERATOR,
    EquipmentCategory.GENERATOR,
}
TURBINES = {EquipmentCategory.GAS_TURBINE}
ROLE_LABEL = {
    "owns": "owner",
    "occupies": "tenant",
    "operates": "operator",
    "filed_by": "permit holder",
    "develops": "developer",
}


def supplier_by_name(name: str | None) -> Supplier | None:
    return next((s for s in SUPPLIERS if s.name == name), None)


def _power(category: str, count: int, size: float | None, unit: str | None) -> float:
    if not size:
        return 0.0
    if unit == "MW":
        return count * size
    if unit == "kW":
        return count * size / 1000
    return 0.0


def _kind(category) -> str | None:
    if category in GENERATORS:
        return "generators"
    if category in TURBINES:
        return "turbines"
    return None


def site_power(session: Session, site: Site) -> dict:
    """Counted generators and turbines at a site, with MW, and the documents they come from."""
    inv = site_inventory(session, site)
    # unsized: counted units whose size no document gives, so MW is a floor, not a total.
    out = {"generators": 0, "turbines": 0, "mw": 0.0, "unsized": 0, "sources": []}
    for permit in inv.permits:
        used = False
        for row in permit.rows:
            sign = permit.sign(row)
            kind = _kind(row.category)
            if not sign or kind is None:
                continue
            used = True
            size, unit = permit.size(row)
            out[kind] += sign * row.count
            mw = _power(str(row.category), row.count, size, unit)
            out["mw"] += sign * mw
            if not mw:
                out["unsized"] += sign * row.count
        if used:
            out["sources"].append(_doc_ref(permit.signal, permit.permit_number))
    out["mw"] = round(out["mw"], 1)
    return out


def _doc_ref(signal: Signal, permit_number: str | None = None) -> dict:
    url = signal.document.url if signal.document else None
    if url is None:
        url = next((ex.fields.get("url") for ex in signal.extractions), None)
    return {
        "permit": permit_number or signal.permit_number,
        "date": signal.filing_date.isoformat() if signal.filing_date else None,
        "type": str(signal.type),
        "title": signal.title,
        "url": url,
    }


def _site_summary(session: Session, site: Site) -> dict:
    signals = session.scalars(select(Signal).where(Signal.site_id == site.id)).all()
    dated = sorted(
        (s for s in signals), key=lambda s: (s.filing_date or date.min, s.id), reverse=True
    )
    return {
        "id": site.id,
        "name": site.name,
        "county": site.county,
        "status": str(site.status),
        "signal_types": sorted({str(s.type) for s in signals}),
        "signal_count": len(signals),
        "latest_signal": _doc_ref(dated[0]) if dated else None,
        "power": site_power(session, site),
    }


def ticker_companies(session: Session, symbol: str) -> list[Company]:
    tickers = session.scalars(select(Ticker).where(Ticker.symbol == symbol.upper())).all()
    return [t.company for t in tickers]


def _company_ties(session: Session, company: Company) -> dict[int, list[dict]]:
    """site id -> ties (role, confidence, evidence chain) for one company."""
    out: dict[int, list[dict]] = defaultdict(list)
    company_links = session.scalars(
        select(Link).where(
            Link.from_type == RecordType.COMPANY,
            Link.from_id == company.id,
            Link.to_type == RecordType.ENTITY,
            not_rejected(),
        )
    ).all()
    for cl in company_links:
        entity = session.get(Entity, cl.to_id)
        for el in session.scalars(
            select(Link).where(
                Link.from_type == RecordType.ENTITY,
                Link.from_id == entity.id,
                Link.to_type == RecordType.SITE,
                not_rejected(),
            )
        ):
            conf = min(el.confidence, cl.confidence, key=RANK.get)
            out[el.to_id].append(
                {
                    "role": ROLE_LABEL.get(str(el.link_type), str(el.link_type)),
                    "company": company.name,
                    "confidence": str(conf),
                    "entity": entity.legal_name,
                    "entity_to_site": {
                        "confidence": str(el.confidence),
                        "method": str(el.method),
                        "evidence_url": el.evidence_url,
                        "note": el.note,
                    },
                    "company_to_entity": {
                        "confidence": str(cl.confidence),
                        "method": str(cl.method),
                        "evidence_url": cl.evidence_url,
                        "note": cl.note,
                    },
                }
            )
    return out


def _best(ties: list[dict]) -> str:
    return max((t["confidence"] for t in ties), key=lambda c: RANK[Confidence(c)])


def _dedupe(ties: list[dict]) -> list[dict]:
    """One tie per (role, entity), the strongest."""
    best: dict[tuple[str, str], dict] = {}
    for t in ties:
        key = (t["role"], t["entity"])
        if (
            key not in best
            or RANK[Confidence(t["confidence"])] > RANK[Confidence(best[key]["confidence"])]
        ):
            best[key] = t
    return sorted(best.values(), key=lambda t: (-RANK[Confidence(t["confidence"])], t["role"]))


def demand_side(session: Session, companies: list[Company]) -> dict:
    ties: dict[int, list[dict]] = defaultdict(list)
    for company in companies:
        for site_id, site_ties in _company_ties(session, company).items():
            ties[site_id].extend(site_ties)
    sites, leads = [], []
    for site_id, site_ties in ties.items():
        site = session.get(Site, site_id)
        row = {**_site_summary(session, site), "ties": _dedupe(site_ties)}
        row["confidence"] = _best(site_ties)
        (leads if row["confidence"] == Confidence.LOW else sites).append(row)
    order = {"high": 0, "medium": 1}
    sites.sort(key=lambda r: (order[r["confidence"]], -r["power"]["mw"], r["name"]))
    leads.sort(key=lambda r: r["name"])
    return {
        "sites": sites,
        "leads": leads,
        "totals": {
            "sites": len(sites),
            "high_confidence_sites": sum(r["confidence"] == "high" for r in sites),
            "generators": sum(r["power"]["generators"] for r in sites),
            "turbines": sum(r["power"]["turbines"] for r in sites),
            "mw": round(sum(r["power"]["mw"] for r in sites), 1),
            "unsized": sum(r["power"]["unsized"] for r in sites),
        },
    }


def supply_side(session: Session, symbol: str) -> dict:
    makers = {s.name for s in SUPPLIERS if s.ticker == symbol.upper()}
    if not makers:
        totals = {"generators": 0, "turbines": 0, "mw": 0.0, "unsized": 0}
        return {"makers": [], "sites": [], "totals": totals}
    sites = []
    for site in session.scalars(select(Site).order_by(Site.name)):
        inv = site_inventory(session, site)
        units: dict[tuple, dict] = {}
        for permit in inv.permits:
            for row in permit.rows:
                sign = permit.sign(row)
                kind = _kind(row.category)
                if not sign or kind is None or row.manufacturer not in makers:
                    continue
                size, unit = permit.size(row)
                key = (kind, row.manufacturer, row.model, size, unit)
                u = units.setdefault(
                    key,
                    {
                        "kind": kind,
                        "manufacturer": row.manufacturer,
                        "model": row.model,
                        "unit_size": size,
                        "unit": unit,
                        "count": 0,
                        "mw": 0.0,
                        "source": {
                            **_doc_ref(permit.signal, permit.permit_number),
                            "page": row.page,
                            "snippet": row.snippet,
                        },
                    },
                )
                u["count"] += sign * row.count
                u["mw"] += sign * _power(kind, row.count, size, unit)
        rows = [u for u in units.values() if u["count"]]
        if rows:
            for u in rows:
                u["mw"] = round(u["mw"], 1)
            sites.append(
                {
                    "id": site.id,
                    "name": site.name,
                    "county": site.county,
                    "status": str(site.status),
                    "units": rows,
                    "generators": sum(u["count"] for u in rows if u["kind"] == "generators"),
                    "turbines": sum(u["count"] for u in rows if u["kind"] == "turbines"),
                    "mw": round(sum(u["mw"] for u in rows), 1),
                }
            )
    sites.sort(key=lambda r: (-r["mw"], r["name"]))
    return {
        "makers": sorted(makers),
        "sites": sites,
        "totals": {
            "generators": sum(s["generators"] for s in sites),
            "turbines": sum(s["turbines"] for s in sites),
            "mw": round(sum(s["mw"] for s in sites), 1),
            "unsized": sum(u["count"] for s in sites for u in s["units"] if not u["unit_size"]),
        },
    }


def recent_signals(demand: dict, supply: dict, limit: int = 10) -> list[dict]:
    seen, out = set(), []
    names = {s["id"]: s["name"] for s in demand["sites"] + supply["sites"]}
    for s in demand["sites"]:
        if s["latest_signal"]:
            out.append({**s["latest_signal"], "site": s["name"], "site_id": s["id"]})
    for s in supply["sites"]:
        for u in s["units"]:
            ref = {k: u["source"][k] for k in ("permit", "date", "type", "title", "url")}
            out.append({**ref, "site": names[s["id"]], "site_id": s["id"]})
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    deduped = []
    for r in out:
        key = (r["site_id"], r["url"], r["permit"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped[:limit]


def ticker_view(session: Session, symbol: str) -> dict | None:
    symbol = symbol.upper()
    companies = ticker_companies(session, symbol)
    makers = [s for s in SUPPLIERS if s.ticker == symbol]
    if not companies and not makers:
        return None
    exchange = session.scalar(select(Ticker.exchange).where(Ticker.symbol == symbol)) or next(
        (s.exchange for s in makers), None
    )
    demand = demand_side(session, companies)
    supply = supply_side(session, symbol)
    names = {c.name for c in companies} | {s.parent or s.name for s in makers}
    return {
        "symbol": symbol,
        "exchange": exchange,
        "names": sorted(names),
        "roles": sorted({str(c.role) for c in companies}),
        "demand": demand,
        "supply": supply,
        "recent_signals": recent_signals(demand, supply),
    }


def ticker_index(session: Session) -> list[dict]:
    """Every ticker with demand-side sites or supplied units, biggest exposure first."""
    symbols = {t.symbol for t in session.scalars(select(Ticker))}
    symbols |= {s.ticker for s in SUPPLIERS if s.ticker}
    out = []
    for symbol in sorted(symbols):
        view = ticker_view(session, symbol)
        if view is None:
            continue
        d, s = view["demand"]["totals"], view["supply"]["totals"]
        if not (d["sites"] or s["generators"] or s["turbines"] or view["demand"]["leads"]):
            continue
        out.append(
            {
                "symbol": symbol,
                "exchange": view["exchange"],
                "names": view["names"],
                "sites": d["sites"],
                "high_confidence_sites": d["high_confidence_sites"],
                "leads": len(view["demand"]["leads"]),
                "site_mw": d["mw"],
                "supplied_units": s["generators"] + s["turbines"],
                "supplied_mw": s["mw"],
            }
        )
    out.sort(key=lambda r: (-(r["sites"] + r["supplied_units"]), r["symbol"]))
    return out
