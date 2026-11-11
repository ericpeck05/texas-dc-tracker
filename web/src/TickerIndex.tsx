import { useEffect, useState } from "react";
import { mw, num } from "./format";
import type { TickerRow } from "./types";
import { BarList, Evidence } from "./ui";

export function TickerIndex() {
  const [rows, setRows] = useState<TickerRow[] | null>(null);
  const [disclaimer, setDisclaimer] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/tickers")
      .then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)),
      )
      .then((d) => {
        setRows(d.tickers);
        setDisclaimer(d.disclaimer);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="error">Could not load tickers: {error}</p>;
  if (!rows) return <p className="dim pad">Loading…</p>;

  const builders = rows.filter((r) => r.sites > 0).sort((a, b) => b.sites - a.sites);
  const suppliers = rows
    .filter((r) => r.supplied_units > 0)
    .sort((a, b) => b.supplied_units - a.supplied_units);
  const secOnly = rows.filter((r) => !r.sites && !r.supplied_units && r.sec_named_sites);
  const label = (r: TickerRow) => (
    <a href={`#/t/${r.symbol}`} className="co-name">
      {r.names[0] ?? r.symbol} <span className="sym">{r.symbol}</span>
    </a>
  );

  return (
    <main>
      <h1 className="page-title">Companies</h1>
      <p className="page-sub">
        Public companies behind Texas data center sites, and the ones whose generators and
        turbines go into them. Click one for its sites.
      </p>
      <div className="cards">
        <section className="card">
          <h3>Building in Texas · sites tied to each company</h3>
          <BarList
            rows={builders.map((r) => ({
              key: r.symbol,
              label: label(r),
              value: r.sites,
              sub: [
                r.site_mw ? `${mw(r.site_mw)} MW on-site power` : null,
                r.leads ? `${r.leads} more possible` : null,
              ]
                .filter(Boolean)
                .join(" · "),
            }))}
          />
        </section>
        {suppliers.length > 0 && (
          <section className="card">
            <h3>Supplying power equipment · generators and turbines in permits</h3>
            <BarList
              rows={suppliers.map((r) => ({
                key: r.symbol,
                label: label(r),
                value: r.supplied_units,
                sub: r.supplied_mw ? `${mw(r.supplied_mw)} MW` : undefined,
              }))}
            />
          </section>
        )}
      </div>
      {secOnly.length > 0 && (
        <p className="dim small">
          Also named in their own SEC filings about a Texas site:{" "}
          {secOnly.map((r, i) => (
            <span key={r.symbol}>
              {i > 0 && ", "}
              <a href={`#/t/${r.symbol}`}>{r.names[0] ?? r.symbol}</a>
            </span>
          ))}
          .
        </p>
      )}
      <Evidence title="Full table" hint="confidence levels, leads, SEC mentions, MW">
        <p className="dim small">
          Medium and high confidence only; low confidence ties are counted as leads. "In SEC
          filings" counts sites the company's own filings name, apart from any role.
        </p>
      <table className="grid">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company</th>
            <th className="r">Sites</th>
            <th className="r">High conf.</th>
            <th className="r">Leads</th>
            <th className="r">In SEC filings</th>
            <th className="r">Site MW</th>
            <th className="r">Units supplied</th>
            <th className="r">Supplied MW</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.symbol}
              onClick={() => (window.location.hash = `#/t/${r.symbol}`)}
            >
              <td>
                <a href={`#/t/${r.symbol}`} className="sym">
                  {r.symbol}
                </a>
                <span className="dim exch">{r.exchange}</span>
              </td>
              <td>{r.names.join(", ")}</td>
              <td className="r mono">{r.sites ? num(r.sites) : "–"}</td>
              <td className="r mono">
                {r.high_confidence_sites ? num(r.high_confidence_sites) : "–"}
              </td>
              <td className="r mono dim">{r.leads ? num(r.leads) : "–"}</td>
              <td className="r mono">
                {r.sec_named_sites ? num(r.sec_named_sites) : "–"}
              </td>
              <td className="r mono">{r.site_mw ? mw(r.site_mw) : "–"}</td>
              <td className="r mono">
                {r.supplied_units ? num(r.supplied_units) : "–"}
              </td>
              <td className="r mono">
                {r.supplied_mw ? mw(r.supplied_mw) : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </Evidence>

      <footer className="disclaimer">{disclaimer}</footer>
    </main>
  );
}
