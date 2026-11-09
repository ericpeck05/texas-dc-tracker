import { useEffect, useMemo, useState } from "react";
import { Conf, fetchJson, isNew, mw, num, signalLabel, statusLabel } from "./format";
import type { SiteRow } from "./types";

type SortKey = "name" | "county" | "status" | "kinds" | "latest_date" | "generators" | "mw";

const STATUS_ORDER = ["operating", "under_construction", "approved", "filed", "rumored", "cancelled"];

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
  const [multi, setMulti] = useState(false);
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
    () => [...new Set((rows ?? []).map((r) => r.county).filter(Boolean) as string[])].sort(),
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
            (c) => c.company.toLowerCase().includes(q) || c.ticker?.toLowerCase() === q,
          )) &&
        (!county || r.county === county) &&
        (!status || r.status === status) &&
        (!multi || r.signal_types.length >= 2),
    );
    out.sort((a, b) => {
      const x = sortValue(a, sort.key);
      const y = sortValue(b, sort.key);
      const c = x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name);
      return sort.desc ? -c : c;
    });
    return out;
  }, [rows, query, county, status, multi, sort]);

  if (error) return <p className="error">Could not load sites: {error}</p>;
  if (!rows) return <p className="dim pad">Loading sites…</p>;

  const multiCount = rows.filter((r) => r.signal_types.length >= 2).length;
  const th = (key: SortKey, label: string, right = false) => (
    <th
      className={`sortable${right ? " r" : ""}${sort.key === key ? " sorted" : ""}`}
      onClick={() =>
        setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "name" && key !== "county" }))
      }
    >
      {label}
      {sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <main>
      <h1 className="page-title">Sites</h1>
      <p className="dim lede">
        {num(rows.length)} Texas data center sites from TCEQ permits and registry records,
        Comptroller tax filings, and local abatements. {num(multiCount)} have two or more kinds
        of signal. Companies shown at medium or high confidence.
      </p>
      <div className="filters">
        <input
          type="search"
          placeholder="Search site, company, or ticker"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={county} onChange={(e) => setCounty(e.target.value)}>
          <option value="">All counties</option>
          {counties.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {STATUS_ORDER.map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
        <label className="dim">
          <input type="checkbox" checked={multi} onChange={(e) => setMulti(e.target.checked)} /> 2+
          kinds of signal
        </label>
        <span className="dim mono">{num(shown.length)} shown</span>
      </div>
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
              className={`clickable${isNew(r.latest_date) ? " new" : ""}`}
              onClick={() => (window.location.hash = `#/s/${r.id}`)}
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
                    {c.confidence === "medium" && <span className="dim">?</span>}
                  </span>
                ))}
              </td>
              <td className="dim">{r.signal_types.map(signalLabel).join(", ")}</td>
              <td className="r mono">
                {r.generators || r.turbines ? `${num(r.generators)} / ${num(r.turbines)}` : "–"}
              </td>
              <td className="r mono">{r.mw || r.unsized ? mw(r.mw, r.unsized) : "–"}</td>
              <td className={`r mono nowrap${isNew(r.latest_date) ? " accent" : ""}`}>
                {r.latest_date ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="dim small">
        A ? after a company means medium confidence: the tie rests on a name match or a match
        between sources, not a filing that names the company. <Conf level="high" /> ties appear
        on the site page.
      </p>
      <footer className="disclaimer">{disclaimer}</footer>
    </main>
  );
}
