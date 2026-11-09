import { Fragment, useEffect, useState } from "react";
import { Conf, Sourced, isNew, mw, num, signalLabel } from "./format";
import type { DemandSite, LinkStep, TickerView } from "./types";

export function TickerPage({ symbol }: { symbol: string }) {
  const [view, setView] = useState<TickerView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setView(null);
    setError(null);
    fetch(`/api/tickers/${encodeURIComponent(symbol)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
        return r.json();
      })
      .then(setView)
      .catch((e) => setError(String(e.message ?? e)));
  }, [symbol]);

  if (error)
    return (
      <main>
        <a href="#/tickers" className="back">
          ← tickers
        </a>
        <p className="error">{error}</p>
      </main>
    );
  if (!view) return <p className="dim pad">Loading {symbol}…</p>;

  const d = view.demand.totals;
  const s = view.supply.totals;
  const hasSupply = view.supply.makers.length > 0;
  const hasDemand = view.roles.length > 0 || view.demand.leads.length > 0;

  return (
    <main>
      <a href="#/tickers" className="back">
        ← tickers
      </a>
      <div className="ticker-head">
        <h1>
          {view.symbol}
          <span className="dim exch">{view.exchange}</span>
        </h1>
        <div className="names">
          {view.names.join(", ")}
          {view.roles.length > 0 && <span className="dim"> · {view.roles.join(", ")}</span>}
          {hasSupply && <span className="dim"> · makes {view.supply.makers.join(", ")}</span>}
        </div>
      </div>

      <section className="stats">
        {hasDemand && (
          <>
            <Stat label="Sites" value={num(d.sites)} sub={`${d.high_confidence_sites} high conf.`} />
            <Stat label="Generators at sites" value={num(d.generators)} />
            <Stat label="Turbines at sites" value={num(d.turbines)} />
            <Stat
              label="Site MW (permitted)"
              value={mw(d.mw, d.unsized)}
              sub={d.unsized ? `${d.unsized} units with no size` : undefined}
            />
          </>
        )}
        {hasSupply && (
          <>
            <Stat label="Units supplied" value={num(s.generators + s.turbines)} accent />
            <Stat label="Supplied MW" value={mw(s.mw, s.unsized)} accent />
          </>
        )}
      </section>

      {view.demand.sites.length > 0 && <DemandTable sites={view.demand.sites} />}
      {hasSupply && <SupplyTable view={view} />}

      {view.demand.leads.length > 0 && (
        <section>
          <h2>
            Leads <span className="dim">· low confidence, not counted above</span>
          </h2>
          <DemandTable sites={view.demand.leads} bare />
        </section>
      )}

      {view.recent_signals.length > 0 && (
        <section>
          <h2>Newest filings</h2>
          <table className="grid">
            <tbody>
              {view.recent_signals.map((r, i) => (
                <tr key={i} className={isNew(r.date) ? "new" : undefined}>
                  <td className="mono nowrap">{r.date ?? "—"}</td>
                  <td>{signalLabel(r.type)}</td>
                  <td>
                    <a href={`#/s/${r.site_id}`}>{r.site}</a>
                  </td>
                  <td className="mono">{r.permit ?? ""}</td>
                  <td>
                    {r.url && (
                      <a href={r.url} target="_blank" rel="noreferrer">
                        document ↗
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <footer className="disclaimer">{view.disclaimer}</footer>
    </main>
  );
}

function Stat(props: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div className="stat">
      <div className="stat-label">{props.label}</div>
      <div className={`stat-value mono${props.accent ? " supply" : ""}`}>{props.value}</div>
      {props.sub && <div className="stat-sub dim">{props.sub}</div>}
    </div>
  );
}

function DemandTable({ sites, bare }: { sites: DemandSite[]; bare?: boolean }) {
  const [open, setOpen] = useState<number | null>(null);
  const table = (
    <table className="grid">
      <thead>
        <tr>
          <th>Site</th>
          <th>County</th>
          <th>Role</th>
          <th>Conf.</th>
          <th className="r">Gen.</th>
          <th className="r">Turb.</th>
          <th className="r">MW</th>
          <th>Signals</th>
          <th>Latest</th>
        </tr>
      </thead>
      <tbody>
        {sites.map((site) => {
          const roles = [...new Set(site.ties.map((t) => t.role))].join(", ");
          const p = site.power;
          const latest = site.latest_signal;
          return (
            <Fragment key={site.id}>
              <tr
                className={`clickable${open === site.id ? " open" : ""}`}
                onClick={() => setOpen(open === site.id ? null : site.id)}
              >
                <td>
                  <span className="caret">{open === site.id ? "▾" : "▸"}</span>{" "}
                  <a href={`#/s/${site.id}`} onClick={(e) => e.stopPropagation()}>
                    {site.name}
                  </a>
                </td>
                <td className="dim">{site.county ?? "—"}</td>
                <td>{roles}</td>
                <td>
                  <Conf level={site.confidence} />
                </td>
                <td className="r mono">
                  {p.generators ? <Sourced docs={p.sources}>{num(p.generators)}</Sourced> : "–"}
                </td>
                <td className="r mono">
                  {p.turbines ? <Sourced docs={p.sources}>{num(p.turbines)}</Sourced> : "–"}
                </td>
                <td className="r mono nowrap">
                  {p.mw || p.unsized ? (
                    <Sourced docs={p.sources}>{mw(p.mw, p.unsized)}</Sourced>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="dim">{site.signal_types.map(signalLabel).join(", ")}</td>
                <td className={`mono nowrap${isNew(latest?.date) ? " accent" : ""}`}>
                  {latest?.url ? (
                    <a href={latest.url} target="_blank" rel="noreferrer">
                      {latest.date ?? "undated"}
                    </a>
                  ) : (
                    (latest?.date ?? "—")
                  )}
                </td>
              </tr>
              {open === site.id && (
                <tr className="detail">
                  <td colSpan={9}>
                    <Evidence site={site} />
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
  if (bare) return table;
  return (
    <section>
      <h2>
        Sites <span className="dim">· owner, tenant, operator, or permit holder · click a row for the evidence</span>
      </h2>
      {table}
    </section>
  );
}

function Step({ label, step }: { label: string; step: LinkStep }) {
  return (
    <div className="step">
      <Conf level={step.confidence} /> <span className="dim">{label}</span>{" "}
      <span>{step.note ?? step.method.replace("_", " ")}</span>{" "}
      {step.evidence_url && (
        <a href={step.evidence_url} target="_blank" rel="noreferrer">
          source ↗
        </a>
      )}
    </div>
  );
}

function Evidence({ site }: { site: DemandSite }) {
  return (
    <div className="evidence">
      {site.ties.map((t, i) => (
        <div key={i} className="tie">
          <div>
            <span className="strong">{t.entity}</span> <span className="dim">is</span> {t.role}{" "}
            <span className="dim">→ belongs to</span> <span className="strong">{t.company}</span>{" "}
            <Conf level={t.confidence} />
          </div>
          <Step label="entity → site:" step={t.entity_to_site} />
          <Step label="company → entity:" step={t.company_to_entity} />
        </div>
      ))}
      {site.power.sources.length > 0 && (
        <div className="tie">
          <span className="dim">Equipment counted from:</span>{" "}
          {site.power.sources.map((src, i) => (
            <span key={i}>
              {i > 0 && " · "}
              {src.url ? (
                <a href={src.url} target="_blank" rel="noreferrer">
                  permit {src.permit} ({src.date ?? "undated"})
                </a>
              ) : (
                `permit ${src.permit}`
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function SupplyTable({ view }: { view: TickerView }) {
  return (
    <section>
      <h2>
        Equipment supplied <span className="dim">· {view.supply.makers.join(", ")} units named in air permits, each counted once</span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Site</th>
            <th>Make / model</th>
            <th>Type</th>
            <th className="r">Units</th>
            <th className="r">Unit size</th>
            <th className="r">MW</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {view.supply.sites.flatMap((site) =>
            site.units.map((u, i) => (
              <tr key={`${site.id}-${i}`}>
                <td>{i === 0 ? <a href={`#/s/${site.id}`}>{site.name}</a> : ""}</td>
                <td>
                  {u.manufacturer} <span className="dim">{u.model ?? ""}</span>
                </td>
                <td className="dim">{u.kind === "turbines" ? "turbine" : "generator"}</td>
                <td className="r mono">
                  <Sourced docs={[u.source]}>{num(u.count)}</Sourced>
                </td>
                <td className="r mono">{u.unit_size ? `${num(u.unit_size)} ${u.unit}` : "—"}</td>
                <td className="r mono">{u.mw ? mw(u.mw) : "—"}</td>
                <td className="dim" title={u.source.snippet ?? ""}>
                  {u.source.url ? (
                    <a href={u.source.url} target="_blank" rel="noreferrer">
                      permit {u.source.permit}
                      {u.source.page ? `, p. ${u.source.page}` : ""}
                    </a>
                  ) : (
                    `permit ${u.source.permit}`
                  )}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </section>
  );
}
