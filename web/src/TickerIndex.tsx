import { useEffect, useState } from "react";
import { mw, num } from "./format";
import type { TickerRow } from "./types";

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

  return (
    <main>
      <h1 className="page-title">Tickers</h1>
      <p className="dim lede">
        Public companies tied to Texas data center sites (owner, tenant,
        operator, permit holder) or supplying their generators and turbines.
        Medium and high confidence only; low confidence ties are counted as
        leads.
      </p>
      <table className="grid">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company</th>
            <th className="r">Sites</th>
            <th className="r">High conf.</th>
            <th className="r">Leads</th>
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
      <footer className="disclaimer">{disclaimer}</footer>
    </main>
  );
}
