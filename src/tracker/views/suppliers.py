"""Supplier scoreboard: generators and turbines by manufacturer, by quarter.

Answers "who is winning the equipment orders?". Counts are the netted units from
rollup.site_inventory (each unit once per site, removed units subtracted), so the
scoreboard adds up to the same totals as the site and ticker pages.

A unit's quarter is when its permit was first filed: the earliest dated document for that
permit number at the site. That is the closest public sign of when the order was placed.
Units a later revision adds are credited to the permit's first quarter too.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import Signal, Site
from tracker.reference.suppliers import SUPPLIERS
from tracker.rollup import site_inventory
from tracker.views.ticker import _doc_ref, _kind, _power

UNKNOWN = "Make not stated"


def quarter(d: date | None) -> str | None:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}" if d else None


def _first_filed(session: Session, site: Site) -> dict[str | None, date | None]:
    out: dict[str | None, date | None] = {}
    for sig in session.scalars(select(Signal).where(Signal.site_id == site.id)):
        if sig.filing_date is None:
            continue
        seen = out.get(sig.permit_number)
        if seen is None or sig.filing_date < seen:
            out[sig.permit_number] = sig.filing_date
    return out


def unit_rows(session: Session) -> list[dict]:
    """Every counted generator or turbine row, with its site, permit quarter, and source."""
    rows = []
    for site in session.scalars(select(Site).order_by(Site.id)):
        inv = site_inventory(session, site)
        if not inv.permits:
            continue
        first = _first_filed(session, site)
        for permit in inv.permits:
            filed = first.get(permit.permit_number) or permit.signal.filing_date
            for row in permit.rows:
                sign = permit.sign(row)
                kind = _kind(row.category)
                if not sign or kind is None:
                    continue
                size, unit = permit.size(row)
                rows.append(
                    {
                        "maker": row.manufacturer or UNKNOWN,
                        "model": row.model,
                        "kind": kind,
                        "count": sign * row.count,
                        "unit_size": size,
                        "unit": unit,
                        "mw": round(sign * _power(kind, row.count, size, unit), 2),
                        "unsized": sign * row.count if not size else 0,
                        "quarter": quarter(filed),
                        "first_filed": filed.isoformat() if filed else None,
                        "site": {"id": site.id, "name": site.name, "status": str(site.status)},
                        "source": {
                            **_doc_ref(permit.signal, permit.permit_number),
                            "page": row.page,
                            "snippet": row.snippet,
                        },
                    }
                )
    return rows


def scoreboard(session: Session) -> dict:
    rows = unit_rows(session)
    info = {s.name: s for s in SUPPLIERS}
    makers: dict[str, dict] = {}
    for r in rows:
        m = makers.get(r["maker"])
        if m is None:
            sup = info.get(r["maker"])
            m = makers[r["maker"]] = {
                "maker": r["maker"],
                "ticker": sup.ticker if sup else None,
                "exchange": sup.exchange if sup else None,
                "parent": sup.parent if sup else None,
                "known": r["maker"] != UNKNOWN,
                "generators": 0,
                "turbines": 0,
                "mw": 0.0,
                "unsized": 0,
                "site_ids": set(),
                "quarters": defaultdict(lambda: {"generators": 0, "turbines": 0, "mw": 0.0}),
                "models": defaultdict(lambda: {"count": 0, "mw": 0.0, "kind": None}),
                "rows": [],
            }
        m[r["kind"]] += r["count"]
        m["mw"] += r["mw"]
        m["unsized"] += r["unsized"]
        m["site_ids"].add(r["site"]["id"])
        q = m["quarters"][r["quarter"] or "undated"]
        q[r["kind"]] += r["count"]
        q["mw"] += r["mw"]
        model = m["models"][r["model"] or "model not stated"]
        model["count"] += r["count"]
        model["mw"] += r["mw"]
        model["kind"] = r["kind"]
        m["rows"].append(r)

    out = []
    for m in makers.values():
        if not (m["generators"] or m["turbines"]):
            continue  # netted to zero
        m["sites"] = len(m.pop("site_ids"))
        m["mw"] = round(m["mw"], 1)
        m["quarters"] = {
            k: {**v, "mw": round(v["mw"], 1)} for k, v in sorted(m["quarters"].items())
        }
        m["models"] = sorted(
            (
                {"model": k, **v, "mw": round(v["mw"], 1)}
                for k, v in m["models"].items()
                if v["count"]
            ),
            key=lambda x: (-x["mw"], -x["count"]),
        )
        m["rows"].sort(key=lambda r: (r["first_filed"] or "", r["site"]["name"]), reverse=True)
        out.append(m)
    out.sort(key=lambda m: (not m["known"], -m["mw"], -(m["generators"] + m["turbines"])))

    known = [m for m in out if m["known"]]
    totals = {
        k: round(sum(m[k] for m in out), 1) for k in ("generators", "turbines", "mw", "unsized")
    }
    known_totals = {k: round(sum(m[k] for m in known), 1) for k in ("generators", "turbines", "mw")}
    quarters = sorted({q for m in out for q in m["quarters"] if q != "undated"})
    return {
        "makers": out,
        "quarters": quarters,
        "totals": totals,
        "known_totals": known_totals,
    }
