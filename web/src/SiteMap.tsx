import { useEffect, useMemo, useRef, useState } from "react";
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
const W = texas.width;
const H = texas.height;
const PAD = 10;
const FULL: Box = { x: -PAD, y: -PAD, w: W + 2 * PAD, h: H + 2 * PAD };
const MAX_ZOOM = 40;
const LABEL_ZOOM = 2.5; // site and county names appear from here in
const MAX_LABELS = 80;

type Box = { x: number; y: number; w: number; h: number };
type Dot = { row: SiteRow; x0: number; y0: number; i: number; r: number };

function project(lat: number, lon: number): [number, number] {
  return [(lon - P.lon_min) * P.kx * P.scale, (P.lat_max - lat) * P.scale];
}

// Map boxes for the metros most sites sit in, as [south, west, north, east].
const AREAS: { name: string; bounds: [number, number, number, number] }[] = [
  { name: "Dallas–Fort Worth", bounds: [32.35, -97.75, 33.45, -96.3] },
  { name: "Austin", bounds: [29.95, -98.2, 30.85, -97.2] },
  { name: "San Antonio", bounds: [29.1, -99.05, 29.8, -98.15] },
  { name: "Houston", bounds: [29.3, -95.95, 30.25, -94.9] },
  { name: "West Texas", bounds: [30.6, -104.2, 33.2, -99.4] },
];

function boxFor(points: [number, number][], margin = 0.15): Box {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  let x = Math.min(...xs);
  let y = Math.min(...ys);
  let w = Math.max(...xs) - x;
  let h = Math.max(...ys) - y;
  // Keep the map's shape, and never zoom in further than MAX_ZOOM.
  const aspect = FULL.w / FULL.h;
  const minW = FULL.w / MAX_ZOOM;
  w = Math.max(w * (1 + 2 * margin), minW);
  h = Math.max(h * (1 + 2 * margin), minW / aspect);
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;
  x = (Math.min(...xs) + Math.max(...xs)) / 2 - w / 2;
  y = (Math.min(...ys) + Math.max(...ys)) / 2 - h / 2;
  return clamp({ x, y, w, h });
}

function clamp(b: Box): Box {
  const w = Math.min(FULL.w, Math.max(FULL.w / MAX_ZOOM, b.w));
  const h = (w * FULL.h) / FULL.w;
  const x = Math.min(Math.max(b.x, FULL.x - w * 0.25), FULL.x + FULL.w - w * 0.75);
  const y = Math.min(Math.max(b.y, FULL.y - h * 0.25), FULL.y + FULL.h - h * 0.75);
  return { x, y, w, h };
}

function shortName(name: string): string {
  return name.length > 28 ? name.slice(0, 27) + "…" : name;
}

function radius(r: SiteRow): number {
  return r.mw > 0 ? Math.min(4 + Math.sqrt(r.mw) * 0.9, 26) : 4;
}

// Sites placed at the same city or county point would hide each other: they fan out on a
// spiral around the point (positions are approximate anyway). Spacing is set per zoom.
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
      dots.push({ row, x0, y0, i, r: radius(row) });
    });
  }
  return dots.sort((a, b) => b.r - a.r); // small dots on top
}

// County label positions: the middle of each county's outline.
const COUNTY_CENTERS = texas.counties.map((c) => {
  const nums = c.d.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
  let x0 = Infinity,
    y0 = Infinity,
    x1 = -Infinity,
    y1 = -Infinity;
  for (let i = 0; i + 1 < nums.length; i += 2) {
    x0 = Math.min(x0, nums[i]);
    x1 = Math.max(x1, nums[i]);
    y0 = Math.min(y0, nums[i + 1]);
    y1 = Math.max(y1, nums[i + 1]);
  }
  return { name: c.name, x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
});

export function SiteMap(props: {
  rows: SiteRow[];
  hovered: number | null;
  onHover: (id: number | null) => void;
}) {
  const { rows, hovered, onHover } = props;
  const dots = useMemo(() => layout(rows), [rows]);
  const [view, setView] = useState<Box>(FULL);
  const [tip, setTip] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const anim = useRef<number | null>(null);
  const drag = useRef<{
    pointers: Map<number, { x: number; y: number }>;
    moved: boolean;
    pinch: number | null;
  }>({ pointers: new Map(), moved: false, pinch: null });

  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || 600));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const height = (width * FULL.h) / FULL.w;
  const k = FULL.w / view.w; // zoom factor
  const px = view.w / width; // map units per screen pixel

  // Screen pixel -> map point.
  const toMap = (clientX: number, clientY: number, b: Box = viewRef.current) => {
    const rect = svg.current!.getBoundingClientRect();
    return [
      b.x + ((clientX - rect.left) / rect.width) * b.w,
      b.y + ((clientY - rect.top) / rect.height) * b.h,
    ];
  };

  const zoomAt = (factor: number, mx: number, my: number) => {
    const b = viewRef.current;
    const w = b.w / factor;
    const next = clamp({
      x: mx - ((mx - b.x) / b.w) * w,
      y: my - ((my - b.y) / b.h) * ((w * FULL.h) / FULL.w),
      w,
      h: 0,
    });
    stopAnim();
    setView(next);
  };

  const stopAnim = () => {
    if (anim.current) cancelAnimationFrame(anim.current);
    anim.current = null;
  };

  const flyTo = (target: Box) => {
    stopAnim();
    const from = viewRef.current;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / 350);
      const e = 1 - Math.pow(1 - t, 3);
      // Interpolate the zoom on a log scale so it feels even.
      const w = Math.exp(Math.log(from.w) + (Math.log(target.w) - Math.log(from.w)) * e);
      const cx = from.x + from.w / 2 + (target.x + target.w / 2 - from.x - from.w / 2) * e;
      const cy = from.y + from.h / 2 + (target.y + target.h / 2 - from.y - from.h / 2) * e;
      const h = (w * FULL.h) / FULL.w;
      setView({ x: cx - w / 2, y: cy - h / 2, w, h });
      anim.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    anim.current = requestAnimationFrame(step);
  };

  // Wheel and trackpad pinch zoom at the cursor. It needs a non-passive listener to stop the
  // page from scrolling at the same time.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [mx, my] = toMap(e.clientX, e.clientY);
      const speed = e.ctrlKey ? 0.01 : 0.0015; // pinch gestures arrive as ctrl+wheel
      zoomAt(Math.exp(-e.deltaY * speed), mx, my);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    drag.current.moved = false;
    drag.current.pinch = null;
    stopAnim();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const prev = d.pointers.get(e.pointerId);
    if (!prev) return;
    const pts = [...d.pointers.entries()];
    if (pts.length === 2) {
      // Two fingers: zoom by the change in their distance, around their midpoint.
      const other = pts.find(([id]) => id !== e.pointerId)![1];
      const dist = Math.hypot(e.clientX - other.x, e.clientY - other.y);
      if (d.pinch) {
        const [mx, my] = toMap((e.clientX + other.x) / 2, (e.clientY + other.y) / 2);
        zoomAt(dist / d.pinch, mx, my);
      }
      d.pinch = dist;
      d.moved = true;
    } else {
      const dx = e.clientX - prev.x;
      const dy = e.clientY - prev.y;
      if (!d.moved && Math.abs(dx) + Math.abs(dy) > 3) {
        d.moved = true;
        // Capture only once it is a drag, so a plain click still reaches the dot.
        svg.current!.setPointerCapture(e.pointerId);
      }
      if (d.moved) {
        const rect = svg.current!.getBoundingClientRect();
        const b = viewRef.current;
        setView(clamp({ ...b, x: b.x - (dx / rect.width) * b.w, y: b.y - (dy / rect.height) * b.h }));
        setTip(null);
      }
    }
    d.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };

  const onPointerUp = (e: React.PointerEvent) => {
    drag.current.pointers.delete(e.pointerId);
    drag.current.pinch = null;
  };

  const zoomButton = (factor: number) =>
    zoomAt(factor, view.x + view.w / 2, view.y + view.h / 2);

  const fitShown = () => {
    if (!dots.length) return;
    flyTo(boxFor(dots.map((d) => [d.x0, d.y0])));
  };

  // Each dot keeps its size on screen as you zoom, so crowded points pull apart.
  const placed = dots.map((d) => {
    // Screen size: about the old full-map size, growing a little as you zoom in.
    const grow = 0.55 * Math.sqrt(Math.min(k, 6));
    const r = d.r * grow * px;
    const spread = d.i === 0 ? 0 : 5.5 * Math.sqrt(d.i) * grow * px;
    const angle = d.i * 2.39996;
    return { ...d, x: d.x0 + spread * Math.cos(angle), y: d.y0 + spread * Math.sin(angle), rr: r };
  });

  const inView = (x: number, y: number) =>
    x >= view.x && x <= view.x + view.w && y >= view.y && y <= view.y + view.h;
  // Names in priority order (bigger sites first), skipping any that would overlap a name
  // already placed. Hover still names every dot.
  const labels: (typeof placed)[number][] = [];
  if (k >= LABEL_ZOOM) {
    const taken: [number, number, number, number][] = [];
    const candidates = placed
      .filter((d) => inView(d.x, d.y))
      .sort((a, b) => b.row.mw - a.row.mw || a.i - b.i || a.row.id - b.row.id);
    for (const d of candidates) {
      if (labels.length >= MAX_LABELS) break;
      const sx = (d.x - view.x) / px + d.rr / px + 3;
      const sy = (d.y - view.y) / px - 7;
      const text = shortName(d.row.name);
      const box: [number, number, number, number] = [sx, sy, sx + text.length * 6.2, sy + 13];
      const hit = taken.some(
        (t) => box[0] < t[2] && box[2] > t[0] && box[1] < t[3] && box[3] > t[1],
      );
      if (!hit) {
        taken.push(box);
        labels.push(d);
      }
    }
  }
  const countyLabels =
    k >= LABEL_ZOOM * 0.8 ? COUNTY_CENTERS.filter((c) => inView(c.x, c.y)) : [];

  const activeId = tip ?? hovered;
  const active = placed.find((d) => d.row.id === activeId) ?? null;
  const tipPos = active
    ? {
        left: ((active.x - view.x) / view.w) * width,
        top: ((active.y - view.y) / view.h) * height,
      }
    : null;

  const unplaced = rows.length - dots.length;
  const byCity = dots.filter(
    (d) => d.row.located === "city" || d.row.located === "exact",
  ).length;
  const byName = dots.filter((d) => d.row.located === "name").length;
  const shownInView = placed.filter((d) => inView(d.x, d.y)).length;

  return (
    <figure className="site-map">
      <div className="map-tools">
        <button onClick={() => zoomButton(1.6)} title="Zoom in" aria-label="Zoom in">
          +
        </button>
        <button onClick={() => zoomButton(1 / 1.6)} title="Zoom out" aria-label="Zoom out">
          −
        </button>
        <button onClick={() => flyTo(FULL)} title="Show all of Texas">
          Texas
        </button>
        <button onClick={fitShown} title="Zoom to the sites the filters show">
          Fit shown
        </button>
        <span className="map-areas">
          {AREAS.map((a) => (
            <button
              key={a.name}
              onClick={() => {
                const [s, w, n, e] = a.bounds;
                flyTo(boxFor([project(n, w), project(s, e)], 0));
              }}
            >
              {a.name}
            </button>
          ))}
        </span>
      </div>
      <div className="map-frame">
        <svg
          ref={svg}
          viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
          role="img"
          aria-label="Map of Texas data center sites. Scroll or pinch to zoom, drag to pan."
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onDoubleClick={(e) => {
            const [mx, my] = toMap(e.clientX, e.clientY);
            zoomAt(e.shiftKey ? 1 / 2 : 2, mx, my);
          }}
        >
          <g className="counties">
            {texas.counties.map((c) => (
              <path key={c.name} d={c.d} vectorEffect="non-scaling-stroke">
                <title>{c.name} County</title>
              </path>
            ))}
          </g>
          {countyLabels.length > 0 && (
            <g className="county-labels" style={{ fontSize: 11 * px }}>
              {countyLabels.map((c) => (
                <text key={c.name} x={c.x} y={c.y} textAnchor="middle">
                  {c.name}
                </text>
              ))}
            </g>
          )}
          <g>
            {placed.map((d) => (
              <circle
                key={d.row.id}
                cx={d.x}
                cy={d.y}
                r={d.rr}
                vectorEffect="non-scaling-stroke"
                className={`dot${d.row.located === "county" || d.row.located === "name" ? " approx" : ""}${activeId === d.row.id ? " hot" : ""}`}
                style={{
                  ["--c" as string]: STATUS_COLOR[d.row.status] ?? "#8a8a8a",
                }}
                onPointerEnter={(e) => {
                  if (e.pointerType !== "mouse" || drag.current.pointers.size) return;
                  setTip(d.row.id);
                  onHover(d.row.id);
                }}
                onPointerLeave={() => {
                  setTip(null);
                  onHover(null);
                }}
                onClick={(e) => {
                  if (drag.current.moved) return;
                  // On touch, the first tap shows the name; a second tap opens the site.
                  if ((e.nativeEvent as PointerEvent).pointerType === "touch" && tip !== d.row.id) {
                    setTip(d.row.id);
                    return;
                  }
                  window.location.hash = `#/s/${d.row.id}`;
                }}
              />
            ))}
          </g>
          {labels.length > 0 && (
            <g className="site-labels" style={{ fontSize: 11 * px }}>
              {labels.map((d) => (
                <text key={d.row.id} x={d.x + d.rr + 3 * px} y={d.y + 4 * px}>
                  {shortName(d.row.name)}
                </text>
              ))}
            </g>
          )}
        </svg>
        {active && tipPos && (
          <div
            className={`map-tip${tipPos.top < 100 ? " below" : ""}`}
            style={{
              left: Math.min(Math.max(tipPos.left, 8), width - 8),
              top: tipPos.top,
            }}
          >
            <div className="strong">{active.row.name}</div>
            <div className="dim">
              {statusLabel(active.row.status)} · {active.row.place}
              {active.row.located === "name" ? " (from site name)" : ""}
              {active.row.mw > 0 ? ` · ${mw(active.row.mw, active.row.unsized)} MW` : ""}
            </div>
            {active.row.companies.length > 0 && (
              <div>{active.row.companies.map((c) => c.ticker ?? c.company).join(", ")}</div>
            )}
          </div>
        )}
        <div className="map-zoom mono dim">
          {k > 1.05 ? `${k.toFixed(1)}× · ${num(shownInView)} in view` : "scroll or pinch to zoom · drag to pan"}
        </div>
      </div>
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
          Size is permitted MW (generators and turbines). Names appear once you zoom in; double-click
          zooms in (shift to zoom out). {num(byCity)} sites placed at their city,{" "}
          {num(dots.length - byCity - byName)} at their county's center, and {num(byName)} by a
          place in the site's own name (both ringed): positions are approximate, and sites at the
          same point fan out around it.
          {unplaced > 0 &&
            ` ${num(unplaced)} sites name no place in any filing and are in the table only.`}
        </div>
      </figcaption>
    </figure>
  );
}
