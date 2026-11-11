"""Approximate map positions for sites, from what the filings say about where they are.

Most filings give a city or county, not coordinates. In order of precision:

1. exact: coordinates stored on the site (none yet; parcels or permits may add them).
2. city: the city a TCEQ Central Registry record or air permit names, at the Census
   Gazetteer internal point for that place.
3. county: the county's centroid.
4. name: no filing names a place, but the site's own name does ("Riot Rockdale", "Bexar
   1", "Poolside Data Center, Pecos County I"). Placed at that city, or the county's
   centroid when the name says "County". Common words that are also towns ("Center",
   "Point", "Cottonwood") are never used.

Reference files come from scripts/build_texas_reference.py.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from tracker.db.models import Signal, Site

REF = Path(__file__).resolve().parent / "reference"


def _norm(name: str) -> str:
    return " ".join(name.lower().replace(".", "").split())


@lru_cache(maxsize=1)
def _table(name: str) -> dict[str, tuple[float, float]]:
    with (REF / name).open(newline="") as f:
        return {
            _norm(r[next(iter(r))]): (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)
        }


def _cities(site: Site, signals: list[Signal]) -> list[str]:
    out = [site.city] if site.city else []
    for sig in signals:
        fields = sig.extractions[-1].fields if sig.extractions else {}
        for key in ("city", "nearest_city"):
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


# Texas towns whose names are ordinary words in site names, or name a creek or area
# rather than the town ("Leon Creek" is in San Antonio, "Canadian Breaks" is not in Canadian).
_NOT_A_PLACE = {
    "center",
    "point",
    "west",
    "canadian",
    "cottonwood",
    "hackberry",
    "leon",
    "turkey",
    "ranger",
    "independence",
    "mission",
    "star",
    "horizon",
    "lincoln",
    "winner",
    "legacy",
    "spectrum",
    "flamingo",
    "roman",
    "lightning",
    "goodnight",
}


def place_in_name(name: str) -> tuple[str, str, tuple[float, float]] | None:
    """(kind, place, point) for the longest place the site's name contains, if any."""
    places, counties = _table("tx_places.csv"), _table("tx_counties.csv")
    text = re.sub(r"\([^)]*\)", " ", name)
    words = re.findall(r"[A-Za-z][A-Za-z'.]*", text)
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            key = _norm(phrase)
            if len(key) < 4 or (n == 1 and key in _NOT_A_PLACE):
                continue
            said_county = i + n < len(words) and words[i + n].lower() == "county"
            if said_county and key in counties:
                return "county", f"{phrase.title()} County", counties[key]
            if key in places:
                return "city", phrase.title(), places[key]
            if key in counties:
                return "county", f"{phrase.title()} County", counties[key]
    return None


def site_location(site: Site, signals: list[Signal]) -> dict:
    if site.lat is not None and site.lon is not None:
        return {"lat": site.lat, "lon": site.lon, "located": "exact", "place": site.city}
    places = _table("tx_places.csv")
    for city in _cities(site, signals):
        if _norm(city) in places:
            lat, lon = places[_norm(city)]
            return {"lat": lat, "lon": lon, "located": "city", "place": city.title()}
    if site.county:
        hit = _table("tx_counties.csv").get(_norm(site.county))
        if hit:
            return {
                "lat": hit[0],
                "lon": hit[1],
                "located": "county",
                "place": f"{site.county} County",
            }
    if found := place_in_name(site.name):
        _kind, place, (lat, lon) = found
        return {"lat": lat, "lon": lon, "located": "name", "place": place}
    return {"lat": None, "lon": None, "located": None, "place": None}
