import { useEffect, useMemo, useState } from "react";
import {
  fetchJson,
  isNew,
  mw,
  num,
  signalLabel,
  statusLabel,
} from "./format";
import { SiteMap } from "./SiteMap";
import type { SiteRow } from "./types";
import { Companies, STATUS_LABEL, StatusChip, ago, placePhrase, powerPhrase } from "./ui";

type SortKey =
  "name" | "county" | "status" | "kinds" | "latest_date" | "generators" | "mw";

const STATUS_ORDER = [
  "operating",
  "under_construction",
  "approved",
  "filed",
  "rumored",
  "cancelled",
];

function sortValue(r: SiteRow, key: SortKey): string | number {
  switch (key) {
    case "kinds":
      return r.signal_types.length * 10 + r.sources;
    case "status":
      return STATUS_ORDER.indexOf(r.status);
    case "latest_date":
      return r.latest_date ?? "";
    case "county":
      return r.county ?? "~";
    default:
      return r[key];
  }
}

export function SiteList() {
  const [rows, setRows] = useState<SiteRow[] | null>(null);
  const [disclaimer, setDisclaimer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [county, setCounty] = useState("");
  const [status, setStatus] = useState("");
  const [hovered, setHovered] = useState<number | null>(null);
  const [view, setView] = useState<"list" | "table">("list");
  const [limit, setLimit] = useState(60);
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "latest_date",
    desc: true,
  });

  useEffect(() => {
    fetchJson<{ sites: SiteRow[]; disclaimer: string }>("/api/sites")
      .then((d) => {
        setRows(d.sites);
        setDisclaimer(d.disclaimer);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const counties = useMemo(
    () =>
      [
        ...new Set(
          (rows ?? []).map((r) => r.county).filter(Boolean) as string[],
        ),
      ].sort(),
    [rows],
  );

  const shown = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    const out = rows.filter(
      (r) =>
        (!q ||
          r.name.toLowerCase().includes(q) ||
          r.companies.some(
            (c) =>
              c.company.toLowerCase().includes(q) ||
              c.ticker?.toLowerCase() === q,
          )) &&
        (!county || r.county === county) &&
        (!status || r.status === status),
    );
    out.sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      const c = x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name);
      return sort.desc ? -c : c;
    });
    return out;
  }, [rows, query, county, status, sort]);

  if (error) return <p className="error">Could not load sites: {error}</p>;
  if (!rows) return <p className="dim pad">Loading sites…</p>;

  const th = (key: SortKey, label: string, right = false) => (
    <th
      className={`sortable${right ? " r" : ""}${sort.key === key ? " sorted" : ""}`}
      onClick={() =>
        setSort((s) => ({
          key,
          desc: s.key === key ? !s.desc : key !== "name" && key !== "county",
        }))
      }
    >
      {label}
      {sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </th>
  );

  const statusCounts = STATUS_ORDER.map((st) => ({
    st,
    n: rows.filter((r) => r.status === st).length,
  })).filter((x) => x.n);

  return (
    <main>
      <h1 className="page-title">Map</h1>
      <p className="page-sub">
        {num(rows.length)} Texas data center sites. Hover a site to find it on the map; click
        for its story.
      </p>
      <div className="filters">
        <input
          type="search"
          placeholder="Search a site, company or ticker"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="chip-filter">
          <button className={status === "" ? "here" : ""} onClick={() => setStatus("")}>
            All
          </button>
          {statusCounts.map(({ st, n }) => (
            <button
              key={st}
              className={status === st ? "here" : ""}
              onClick={() => setStatus(status === st ? "" : st)}
              style={{ ["--c" as string]: `var(--st-${st})` }}
            >
              <span className="status-dot" /> {STATUS_LABEL[st]} <span className="dim">{n}</span>
            </button>
          ))}
        </div>
        <select value={county} onChange={(e) => setCounty(e.target.value)}>
          <option value="">All counties</option>
          {counties.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <select
          value={`${sort.key}:${sort.desc ? "d" : "a"}`}
          onChange={(e) => {
            const [key, dir] = e.target.value.split(":");
            setSort({ key: key as SortKey, desc: dir === "d" });
          }}
        >
          <option value="latest_date:d">Newest activity</option>
          <option value="mw:d">Most on-site power</option>
          <option value="status:a">Furthest along</option>
          <option value="name:a">Name</option>
        </select>
        <div className="toggle">
          <button className={view === "list" ? "here" : ""} onClick={() => setView("list")}>
            List
          </button>
          <button className={view === "table" ? "here" : ""} onClick={() => setView("table")}>
            Table
          </button>
        </div>
        <span className="dim small">{num(shown.length)} shown</span>
      </div>
      <div className="map-layout">
        <SiteMap rows={shown} hovered={hovered} onHover={setHovered} />
        <div className="table-side">
          {view === "list" ? (
            <div className="site-items">
              {shown.slice(0, limit).map((r) => {
                const power = powerPhrase(r);
                return (
                  <a
                    key={r.id}
                    href={`#/s/${r.id}`}
                    className={`site-item${hovered === r.id ? " hot" : ""}`}
                    onMouseEnter={() => setHovered(r.id)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <div className="si-top">
                      <span className="si-name">{r.name}</span>
                      <StatusChip status={r.status} />
                    </div>
                    <div className="si-meta">
                      <span>{r.place ?? placePhrase(r.county)}</span>
                      {r.companies.length > 0 && (
                        <>
                          {" · "}
                          <Companies items={r.companies} />
                        </>
                      )}
                    </div>
                    <div className="si-meta dim">
                      {power ? `${power} · ` : ""}
                      {isNew(r.latest_date) ? (
                        <span className="accent">new filing {ago(r.latest_date)}</span>
                      ) : (
                        `last filing ${ago(r.latest_date)}`
                      )}
                    </div>
                  </a>
                );
              })}
              {shown.length > limit && (
                <button className="linkish" onClick={() => setLimit(limit + 60)}>
                  Show more ({num(shown.length - limit)} left)
                </button>
              )}
            </div>
          ) : (
          <table className="grid">
              <thead>
                <tr>
                  {th("name", "Site")}
                  {th("county", "County")}
                  {th("status", "Status")}
                  <th>Companies</th>
                  {th("kinds", "Signals")}
                  {th("generators", "Gen. / turb.", true)}
                  {th("mw", "MW", true)}
                  {th("latest_date", "Latest", true)}
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr
                    key={r.id}
                    className={`clickable${isNew(r.latest_date) ? " new" : ""}${hovered === r.id ? " hot" : ""}`}
                    onClick={() => (window.location.hash = `#/s/${r.id}`)}
                    onMouseEnter={() => setHovered(r.id)}
                    onMouseLeave={() => setHovered(null)}
                  >
                    <td>
                      <a href={`#/s/${r.id}`}>{r.name}</a>
                    </td>
                    <td className="dim">{r.county ?? "—"}</td>
                    <td className="dim nowrap">{statusLabel(r.status)}</td>
                    <td>
                      {r.companies.map((c, i) => (
                        <span key={c.company} className="nowrap">
                          {i > 0 && ", "}
                          {c.ticker ? (
                            <a
                              href={`#/t/${c.ticker}`}
                              className="sym"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {c.ticker}
                            </a>
                          ) : (
                            c.company
                          )}
                          {c.confidence === "medium" && (
                            <span className="dim">?</span>
                          )}
                        </span>
                      ))}
                    </td>
                    <td className="dim">
                      {r.signal_types.map(signalLabel).join(", ")}
                    </td>
                    <td className="r mono">
                      {r.generators || r.turbines
                        ? `${num(r.generators)} / ${num(r.turbines)}`
                        : "–"}
                    </td>
                    <td className="r mono">
                      {r.mw || r.unsized ? mw(r.mw, r.unsized) : "–"}
                    </td>
                    <td
                      className={`r mono nowrap${isNew(r.latest_date) ? " accent" : ""}`}
                    >
                      {r.latest_date ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <footer className="disclaimer">{disclaimer}</footer>
    </main>
  );
}
