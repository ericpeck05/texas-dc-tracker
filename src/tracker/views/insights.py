"""Overview: who is building data centers in Texas, and how fast.

Everything here is a summary of the same records the other pages show:

- A site's start is the date of its earliest public filing we have. Older years are thinner
  partly because some sources only reach back a few years, so compare recent years.
- Power is the on-site generation counted in air permits (backup generators and turbines).
  It is a floor and is not the site's IT load.
- A company counts for a site only when the tie is medium or high confidence, so most sites
  (filed by an LLC or a contractor) have no company yet.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.db.models import Signal, Site
from tracker.views.sites import _site_companies, owner_chain
from tracker.views.ticker import site_power

FIRST_YEAR = 2021  # the pace charts start here; earlier filings are sparse
PACE_TYPES = ("air_permit", "stormwater", "incentive", "grid")
# Roles that say who a project is for. A stormwater "permit holder" is often the contractor.
PROJECT_ROLES = {"owner", "operator", "tenant", "developer", "applicant"}


def quarter(d: date) -> str:
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"


def quarters(start: int, today: date) -> list[str]:
    out, y, q = [], start, 1
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        out.append(f"{y} Q{q}")
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def _site_facts(session: Session, site: Site, signals: list[Signal]) -> dict:
    dated = sorted(s.filing_date for s in signals if s.filing_date)
    power = site_power(session, site)
    power_dates = sorted(s["date"] for s in power["sources"] if s["date"])
    chain = owner_chain(session, site)
    companies = [c for c in _site_companies(chain) if c["confidence"] != "low"]
    others = sorted(
        {
            e["entity"]
            for e in chain
            if not e["companies"] and {r["role"] for r in e["roles"]} & PROJECT_ROLES
        }
    )
    return {
        "id": site.id,
        "name": site.name,
        "county": site.county,
        "status": str(site.status),
        "first": dated[0] if dated else None,
        "latest": dated[-1] if dated else None,
        "mw": power["mw"],
        "units": power["generators"] + power["turbines"],
        "power_first": date.fromisoformat(power_dates[0]) if power_dates else None,
        "companies": [{"company": c["company"], "ticker": c["ticker"]} for c in companies],
        "others": others,
    }


def _ref(f: dict) -> dict:
    return {
        "id": f["id"],
        "name": f["name"],
        "county": f["county"],
        "status": f["status"],
        "mw": f["mw"],
        "units": f["units"],
        "first": f["first"].isoformat() if f["first"] else None,
    }


def _window(d: date | None, start: date, end: date) -> bool:
    return d is not None and start < d <= end


def _companies(facts: list[dict], year_ago: date, today: date) -> list[dict]:
    rows: dict[str, dict] = {}
    for f in facts:
        for c in f["companies"]:
            row = rows.setdefault(
                c["company"],
                {
                    "company": c["company"],
                    "ticker": c["ticker"],
                    "sites": [],
                    "new_12m": 0,
                    "mw": 0.0,
                    "status": Counter(),
                    "first": None,
                    "latest": None,
                },
            )
            row["sites"].append(_ref(f))
            row["mw"] += f["mw"]
            row["status"][f["status"]] += 1
            row["new_12m"] += _window(f["first"], year_ago, today)
            if f["first"] and (row["first"] is None or f["first"] < row["first"]):
                row["first"] = f["first"]
            if f["latest"] and (row["latest"] is None or f["latest"] > row["latest"]):
                row["latest"] = f["latest"]
    out = []
    for row in rows.values():
        row["sites"].sort(key=lambda s: (-(s["mw"] or 0), s["first"] or "", s["name"]))
        out.append(
            {
                **row,
                "mw": round(row["mw"], 1),
                "status": dict(row["status"]),
                "first": row["first"].isoformat() if row["first"] else None,
                "latest": row["latest"].isoformat() if row["latest"] else None,
            }
        )
    return sorted(out, key=lambda r: (-len(r["sites"]), -r["mw"], r["company"]))


def _pace(facts: list[dict], signals: list[Signal], today: date) -> list[dict]:
    qs = quarters(FIRST_YEAR, today)
    rows = {
        q: {"quarter": q, "new_sites": 0, "units": 0, "mw": 0.0, **{t: 0 for t in PACE_TYPES}}
        for q in qs
    }
    for f in facts:
        if f["first"] and (q := quarter(f["first"])) in rows:
            rows[q]["new_sites"] += 1
        if f["power_first"] and (q := quarter(f["power_first"])) in rows:
            rows[q]["mw"] += f["mw"]
            rows[q]["units"] += f["units"]
    for s in signals:
        if s.filing_date and str(s.type) in PACE_TYPES and (q := quarter(s.filing_date)) in rows:
            rows[q][str(s.type)] += 1
    out = [{**r, "mw": round(r["mw"], 1)} for r in rows.values()]
    out[-1]["partial"] = True  # the current quarter is not over
    return out


def _counties(facts: list[dict], year_ago: date, today: date) -> list[dict]:
    rows: dict[str, dict] = defaultdict(lambda: {"sites": 0, "new_12m": 0, "mw": 0.0})
    for f in facts:
        if not f["county"]:
            continue
        r = rows[f["county"]]
        r["sites"] += 1
        r["new_12m"] += _window(f["first"], year_ago, today)
        r["mw"] += f["mw"]
    out = [{"county": k, **v, "mw": round(v["mw"], 1)} for k, v in rows.items()]
    return sorted(out, key=lambda r: (-r["sites"], -r["mw"], r["county"]))


def _by_year(facts: list[dict]) -> Counter:
    return Counter(f["first"].year for f in facts if f["first"])


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _takeaways(
    facts: list[dict], companies: list[dict], counties: list[dict], today: date
) -> list[str]:
    out = []
    years = _by_year(facts)
    this, last, before = years[today.year], years[today.year - 1], years[today.year - 2]
    if this or last:
        line = (
            f"{_plural(this, 'site')} had a first public filing in {today.year} so far, "
            f"against {last:,} in all of {today.year - 1}"
        )
        if before:
            line += f" and {before:,} in {today.year - 2}"
        out.append(line + ".")
    with_units = [f for f in facts if f["units"] > 0]
    if with_units:
        out.append(
            f"Air permits count {sum(f['units'] for f in with_units):,} generators and turbines "
            f"at {_plural(len(with_units), 'site')}."
        )
    powered = sorted((f for f in facts if f["mw"] > 0), key=lambda f: -f["mw"])
    total = sum(f["mw"] for f in powered)
    if powered:
        top = powered[:3]
        share = sum(f["mw"] for f in top) / total if total else 0
        out.append(
            f"Only {_plural(len(powered), 'site')} give unit sizes; they add up to "
            f"{total:,.0f} MW, and the largest three ("
            + ", ".join(f"{f['name']} {f['mw']:,.0f} MW" for f in top)
            + f") hold {share:.0%} of it."
        )
    if companies:
        lead = ", ".join(f"{c['company']} ({len(c['sites'])})" for c in companies[:4])
        out.append(f"Tied to the most sites: {lead}.")
        newest = sorted(
            (c for c in companies if c["new_12m"]), key=lambda c: (-c["new_12m"], c["company"])
        )[:3]
        if newest:
            out.append(
                "Most new sites in the last 12 months: "
                + ", ".join(f"{c['company']} ({c['new_12m']})" for c in newest)
                + "."
            )
    named = sum(1 for f in facts if f["companies"])
    if facts:
        out.append(
            f"{named} of {len(facts)} sites ({named / len(facts):.0%}) are tied to a named "
            "company; the rest are filed by project LLCs or contractors that are not yet traced "
            "to a parent."
        )
    if len(counties) >= 3:
        c1, c2, c3 = counties[:3]
        out.append(
            f"Busiest counties: {c1['county']} ({c1['sites']}), {c2['county']} ({c2['sites']}), "
            f"{c3['county']} ({c3['sites']})."
        )
    return out


def insights(session: Session, today: date | None = None) -> dict:
    today = today or date.today()
    year_ago = today - timedelta(days=365)
    two_years_ago = today - timedelta(days=730)
    all_signals = session.scalars(select(Signal)).all()
    by_site: dict[int, list[Signal]] = defaultdict(list)
    for s in all_signals:
        if s.site_id:
            by_site[s.site_id].append(s)
    facts = [
        _site_facts(session, site, by_site[site.id])
        for site in session.scalars(select(Site).order_by(Site.name))
        if by_site.get(site.id)
    ]
    companies = _companies(facts, year_ago, today)
    counties = _counties(facts, year_ago, today)
    status = Counter(f["status"] for f in facts)
    others: dict[str, list[dict]] = defaultdict(list)
    for f in facts:
        if not f["companies"]:
            for name in f["others"]:
                others[name].append(_ref(f))
    return {
        "as_of": today.isoformat(),
        "totals": {
            "sites": len(facts),
            "named": sum(1 for f in facts if f["companies"]),
            "new_12m": sum(_window(f["first"], year_ago, today) for f in facts),
            "new_prior_12m": sum(_window(f["first"], two_years_ago, year_ago) for f in facts),
            "mw": round(sum(f["mw"] for f in facts), 1),
            "powered_sites": sum(1 for f in facts if f["mw"] > 0),
            "units": sum(f["units"] for f in facts),
            "unit_sites": sum(1 for f in facts if f["units"] > 0),
            "status": dict(status),
        },
        "takeaways": _takeaways(facts, companies, counties, today),
        "pace": _pace(facts, all_signals, today),
        "by_year": [{"year": y, "new_sites": n} for y, n in sorted(_by_year(facts).items())],
        "companies": companies,
        "other_names": sorted(
            ({"entity": k, "sites": v} for k, v in others.items()),
            key=lambda r: (-len(r["sites"]), r["entity"]),
        ),
        "counties": counties,
        "newest": [
            _ref(f)
            for f in sorted(
                (f for f in facts if f["first"]), key=lambda f: (f["first"], f["id"]), reverse=True
            )[:12]
        ],
    }
