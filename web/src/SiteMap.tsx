import { useMemo, useState } from "react";
import { mw, num, statusLabel } from "./format";
import texas from "./texas-map.json";
import type { SiteRow } from "./types";

export const STATUS_COLOR: Record<string, string> = {
  operating: "#e8e8e8",
  under_construction: "#e07a5f",
  approved: "#a8c686",
  filed: "#8a8a8a",
  rumored: "#555555",
  cancelled: "#3a3a3a",
};

const P = texas.projection;

function project(lat: number, lon: number): [number, number] {
  return [(lon - P.lon_min) * P.kx * P.scale, (P.lat_max - lat) * P.scale];
}

function radius(r: SiteRow): number {
  return r.mw > 0 ? Math.min(4 + Math.sqrt(r.mw) * 0.9, 26) : 4;
}

type Dot = { row: SiteRow; x: number; y: number; r: number };

// Sites placed at the same city or county point would hide each other: fan them out on a
// small spiral around the point (positions are approximate anyway).
function layout(rows: SiteRow[]): Dot[] {
  const groups = new Map<string, SiteRow[]>();
  for (const row of rows) {
    if (row.lat == null || row.lon == null) continue;
    const key = `${row.lat.toFixed(3)},${row.lon.toFixed(3)}`;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  const dots: Dot[] = [];
  for (const group of groups.values()) {
    group.sort((a, b) => b.mw - a.mw || a.id - b.id);
    group.forEach((row, i) => {
      const [x0, y0] = project(row.lat!, row.lon!);
      const angle = i * 2.39996;
      const dist = i === 0 ? 0 : 5 * Math.sqrt(i);
      dots.push({
        row,
        x: x0 + dist * Math.cos(angle),
        y: y0 + dist * Math.sin(angle),
        r: radius(row),
      });
    });
  }
  return dots.sort((a, b) => b.r - a.r); // small dots on top
}

export function SiteMap(props: {
  rows: SiteRow[];
  hovered: number | null;
  onHover: (id: number | null) => void;
}) {
  const { rows, hovered, onHover } = props;
  const dots = useMemo(() => layout(rows), [rows]);
  const [tip, setTip] = useState<Dot | null>(null);
  const unplaced = rows.length - dots.length;
  const byCity = dots.filter(
    (d) => d.row.located === "city" || d.row.located === "exact",
  ).length;
  const byName = dots.filter((d) => d.row.located === "name").length;
  const active = tip ?? dots.find((d) => d.row.id === hovered) ?? null;

  return (
    <figure className="site-map">
      <svg
        viewBox={`-10 -10 ${texas.width + 20} ${texas.height + 20}`}
        role="img"
        aria-label="Map of Texas data center sites"
      >
        <g className="counties">
          {texas.counties.map((c) => (
            <path key={c.name} d={c.d}>
              <title>{c.name} County</title>
            </path>
          ))}
        </g>
        <g>
          {dots.map((d) => (
            <circle
              key={d.row.id}
              cx={d.x}
              cy={d.y}
              r={d.r}
              className={`dot${d.row.located === "county" || d.row.located === "name" ? " approx" : ""}${hovered === d.row.id ? " hot" : ""}`}
              style={{
                ["--c" as string]: STATUS_COLOR[d.row.status] ?? "#8a8a8a",
              }}
              onMouseEnter={() => {
                setTip(d);
                onHover(d.row.id);
              }}
              onMouseLeave={() => {
                setTip(null);
                onHover(null);
              }}
              onClick={() => (window.location.hash = `#/s/${d.row.id}`)}
            />
          ))}
        </g>
        {active && (
          <g
            className="map-tip"
            transform={`translate(${Math.min(active.x + 12, texas.width - 330)},${Math.max(active.y - 12, 30)})`}
          >
            <rect x={-6} y={-26} width={340} height={58} />
            <text y={-6} className="strong">
              {active.row.name.length > 40
                ? active.row.name.slice(0, 39) + "…"
                : active.row.name}
            </text>
            <text y={18} className="dim">
              {statusLabel(active.row.status)} · {active.row.place}
              {active.row.located === "name" ? " (from site name)" : ""}
              {active.row.mw > 0
                ? ` · ${mw(active.row.mw, active.row.unsized)} MW`
                : ""}
            </text>
          </g>
        )}
      </svg>
      <figcaption>
        <div className="legend">
          {Object.entries(STATUS_COLOR).map(([status, color]) => (
            <span key={status} className="nowrap">
              <svg width="10" height="10">
                <circle cx="5" cy="5" r="4" fill={color} />
              </svg>{" "}
              {statusLabel(status)}
            </span>
          ))}
          <span className="nowrap">
            <svg width="10" height="10">
              <circle
                cx="5"
                cy="5"
                r="4"
                className="dot approx"
                style={{ ["--c" as string]: "#8a8a8a" }}
              />
            </svg>{" "}
            county or site name only
          </span>
        </div>
        <div className="dim small">
          Size is permitted MW (generators and turbines). {num(byCity)} sites
          placed at their city, {num(dots.length - byCity - byName)} at their
          county's center, and {num(byName)} by a place in the site's own name
          (both ringed): positions are approximate.
          {unplaced > 0 &&
            ` ${num(unplaced)} sites name no place in any filing and are in the table only.`}
        </div>
      </figcaption>
    </figure>
  );
}
