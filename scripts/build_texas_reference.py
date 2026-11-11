"""Build the Texas map and place reference files (run once; outputs are committed).

Sources, both official public downloads:
- County shapes: us-atlas 3.0.1 (Census cartographic boundaries, 1:10m) from the npm CDN.
- Place coordinates: Census Gazetteer 2024, Texas places (cities, towns, CDPs).

Writes:
- src/tracker/reference/tx_counties.csv  county, lat, lon (polygon centroid)
- src/tracker/reference/tx_places.csv    place, lat, lon (Census internal point)
- web/src/texas-map.json                 county outlines as SVG paths + the projection

Run: uv run python scripts/build_texas_reference.py
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path

from tracker.http import PoliteClient

ROOT = Path(__file__).resolve().parents[1]
ATLAS_URL = "https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/counties-10m.json"
PLACES_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_gaz_place_48.txt"
)
WIDTH = 1000
LAT0 = 31.0  # Texas mid-latitude, for an equirectangular projection that looks right


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def ring(arcs: list, idxs: list[int]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in idxs:
        seg = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        pts.extend(seg if not pts else seg[1:])
    return pts


def centroid(rings: list[list[tuple[float, float]]]) -> tuple[float, float]:
    """Area-weighted centroid of the outer rings (lon, lat)."""
    a_sum = cx = cy = 0.0
    for r in rings:
        a = 0.0
        x = y = 0.0
        for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1], strict=True):
            f = x0 * y1 - x1 * y0
            a += f
            x += (x0 + x1) * f
            y += (y0 + y1) * f
        if a:
            a_sum += a / 2
            cx += x / 6
            cy += y / 6
    return cx / a_sum, cy / a_sum


def main() -> None:
    with PoliteClient() as client:
        topo = client.get(ATLAS_URL).json()
        places_text = client.get(PLACES_URL).text
    arcs = decode_arcs(topo)
    counties = []
    for geom in topo["objects"]["counties"]["geometries"]:
        if not str(geom["id"]).startswith("48"):
            continue
        polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
        rings = [[ring(arcs, r) for r in poly] for poly in polys]
        counties.append((geom["properties"]["name"], rings))

    lons = [p[0] for _, rs in counties for poly in rs for r in poly for p in r]
    lats = [p[1] for _, rs in counties for poly in rs for r in poly for p in r]
    kx = math.cos(math.radians(LAT0))
    lon_min, lat_max = min(lons), max(lats)
    scale = WIDTH / ((max(lons) - lon_min) * kx)
    height = round((lat_max - min(lats)) * scale)

    def xy(lon: float, lat: float) -> str:
        return f"{(lon - lon_min) * kx * scale:.1f},{(lat_max - lat) * scale:.1f}"

    paths, rows = [], []
    for name, polys in sorted(counties):
        d = "".join("M" + "L".join(xy(*p) for p in r) + "Z" for poly in polys for r in poly)
        paths.append({"name": name, "d": d})
        lon, lat = centroid([poly[0] for poly in polys])
        rows.append((name, round(lat, 5), round(lon, 5)))

    ref = ROOT / "src" / "tracker" / "reference"
    with (ref / "tx_counties.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "lat", "lon"])
        w.writerows(rows)

    places = []
    lines = places_text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        rec = dict(zip(header, (v.strip() for v in line.split("\t")), strict=False))
        name = re.sub(r"\s+(city|town|village|CDP)$", "", rec["NAME"])
        name = re.sub(r"^(?:Town|City|Village) of ", "", name)  # "Town of Pecos city"
        places.append((name, float(rec["INTPTLAT"]), float(rec["INTPTLONG"])))
    with (ref / "tx_places.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["place", "lat", "lon"])
        w.writerows(sorted(places))

    out = {
        "width": WIDTH,
        "height": height,
        "projection": {"lon_min": lon_min, "lat_max": lat_max, "kx": kx, "scale": scale},
        "source": "US Census cartographic boundaries via us-atlas 3.0.1",
        "counties": paths,
    }
    (ROOT / "web" / "src" / "texas-map.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"{len(rows)} counties, {len(places)} places, map {WIDTH}x{height}")


if __name__ == "__main__":
    main()
