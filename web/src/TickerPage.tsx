import { Fragment, useEffect, useState } from "react";
import {
  Conf,
  Sourced,
  isNew,
  mw,
  num,
  signalLabel,
  statusLabel,
} from "./format";
import type { DemandSite, LinkStep, SecNamedSite, TickerView } from "./types";
import {
  BarList,
  Evidence,
  KindTag,
  StatusChip,
  ago,
  longDate,
  placePhrase,
  powerPhrase,
} from "./ui";

export function TickerPage({ symbol }: { symbol: string }) {
  const [view, setView] = useState<TickerView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setView(null);
    setError(null);
    fetch(`/api/tickers/${encodeURIComponent(symbol)}`)
      .then(async (r) => {
        if (!r.ok)
          throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
        return r.json();
      })
      .then(setView)
      .catch((e) => setError(String(e.message ?? e)));
  }, [symbol]);

  if (error)
    return (
      <main>
        <a href="#/tickers" className="back">
          ← Companies
        </a>
        <p className="error">{error}</p>
      </main>
    );
  if (!view) return <p className="dim pad">Loading {symbol}…</p>;

  const d = view.demand.totals;
  const s = view.supply.totals;
  const hasSupply = view.supply.makers.length > 0;
  const hasDemand = view.roles.length > 0 || view.demand.leads.length > 0;

  const RANK: Record<string, number> = { operating: 0, under_construction: 1, approved: 2 };
  const sites = [...view.demand.sites].sort(
    (a, b) =>
      (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) ||
      b.power.mw - a.power.mw ||
      (b.latest_signal?.date ?? "").localeCompare(a.latest_signal?.date ?? ""),
  );
  const byStatus = (st: string) => sites.filter((x) => x.status === st).length;
  const building = byStatus("under_construction");
  const approved = byStatus("approved");
  const name = view.names[0] ?? view.symbol;
  const sitePower = powerPhrase(d);
  const supplied = powerPhrase(s);

  return (
    <main>
      <a href="#/tickers" className="back">
        ← Companies
      </a>
      <div className="page-head">
        <h1 className="page-title">{name}</h1>
        <span className="sym big-sym">
          {view.symbol}
          <span className="dim exch">{view.exchange}</span>
        </span>
      </div>
      <p className="lede-big">
        {hasDemand && d.sites > 0 && (
          <>
            {name} is tied to <strong>{num(d.sites)} Texas data center site{d.sites === 1 ? "" : "s"}</strong>
.{" "}
            {building + approved > 0 &&
              `${building ? `${building} under construction` : ""}${building && approved ? " and " : ""}${approved ? `${approved} approved` : ""}; the rest are at the permit stage. `}
            {sitePower && `Air permits at its sites list ${sitePower}. `}
          </>
        )}
        {hasSupply && supplied && (
          <>
            Its {view.supply.makers.join(", ")} equipment appears in permits for{" "}
            <strong>{supplied}</strong> across {num(view.supply.sites.length)} site
            {view.supply.sites.length === 1 ? "" : "s"}.{" "}
          </>
        )}
        {view.sec_mentions.length > 0 &&
          `Its own SEC filings name ${view.sec_mentions.length} Texas site${view.sec_mentions.length === 1 ? "" : "s"}.`}
      </p>

      {sites.length > 0 && (
        <section>
          <h2>Its sites</h2>
          <div className="site-items grid-2">
            {sites.map((site) => {
              const roles = [...new Set(site.ties.map((t) => t.role))].join(", ");
              const power = powerPhrase(site.power);
              return (
                <a key={site.id} href={`#/s/${site.id}`} className="site-item">
                  <div className="si-top">
                    <span className="si-name">{site.name}</span>
                    <StatusChip status={site.status} />
                  </div>
                  <div className="si-meta">
                    {placePhrase(site.county)} · {roles}
                    {site.confidence === "medium" && <span className="likely">likely</span>}
                  </div>
                  <div className="si-meta dim">
                    {power ? `${power} · ` : ""}last filing {ago(site.latest_signal?.date)}
                  </div>
                </a>
              );
            })}
          </div>
        </section>
      )}

      {hasSupply && (
        <section className="card" style={{ marginTop: 20 }}>
          <h3>Where its equipment goes · units named in air permits</h3>
          <BarList
            rows={view.supply.sites.map((x) => ({
              key: String(x.id),
              label: <a href={`#/s/${x.id}`}>{x.name}</a>,
              value: x.generators + x.turbines,
              sub: x.mw ? `${mw(x.mw)} MW` : undefined,
            }))}
          />
        </section>
      )}

      {view.recent_signals.length > 0 && (
        <section className="card" style={{ marginTop: 20 }}>
          <h3>Newest filings</h3>
          <ol className="timeline">
            {view.recent_signals.slice(0, 6).map((r, i) => (
              <li key={i} className={isNew(r.date) ? "new" : undefined}>
                <div className="tl-date">
                  {r.date ? longDate(r.date) : "Undated"} <KindTag type={r.type} />
                </div>
                <div className="tl-title">
                  <a href={`#/s/${r.site_id}`}>{r.site}</a>
                  {r.url && (
                    <>
                      {" · "}
                      <a href={r.url} target="_blank" rel="noreferrer" className="dim">
                        document ↗
                      </a>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      <Evidence title="Show the evidence" hint="how each site is tied, counts, permits, SEC text">
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
        {sites.length > 0 && <DemandTable sites={sites} />}
        {hasSupply && <SupplyTable view={view} />}
        {view.demand.leads.length > 0 && (
          <section>
            <h2>
              Leads <span className="dim">· low confidence, not counted above</span>
            </h2>
            <DemandTable sites={view.demand.leads} bare />
          </section>
        )}
        {view.sec_mentions.length > 0 && <SecTable sites={view.sec_mentions} />}
      </Evidence>

      <footer className="disclaimer">{view.disclaimer}</footer>
    </main>
  );
}

function SecTable({ sites }: { sites: SecNamedSite[] }) {
  return (
    <section>
      <h2>
        Named in its own SEC filings{" "}
        <span className="dim">
          · shows involvement, not a role; not counted above
        </span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Site</th>
            <th>County</th>
            <th>Filings</th>
            <th>Newest filing says</th>
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => {
            const newest = site.filings[0];
            return (
              <tr key={site.id}>
                <td>
                  <a href={`#/s/${site.id}`}>{site.name}</a>{" "}
                  <span className="dim small">{statusLabel(site.status)}</span>
                </td>
                <td>{site.county ?? "—"}</td>
                <td className="nowrap">
                  {site.filings.map((f, i) => (
                    <div key={i} className="small">
                      <a
                        href={f.url ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {f.form} {f.date}
                      </a>
                    </div>
                  ))}
                </td>
                <td className="small dim">{newest?.snippet}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function Stat(props: {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{props.label}</div>
      <div className={`stat-value mono${props.accent ? " supply" : ""}`}>
        {props.value}
      </div>
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
                  <a
                    href={`#/s/${site.id}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {site.name}
                  </a>
                </td>
                <td className="dim">{site.county ?? "—"}</td>
                <td>{roles}</td>
                <td>
                  <Conf level={site.confidence} />
                </td>
                <td className="r mono">
                  {p.generators ? (
                    <Sourced docs={p.sources}>{num(p.generators)}</Sourced>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="r mono">
                  {p.turbines ? (
                    <Sourced docs={p.sources}>{num(p.turbines)}</Sourced>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="r mono nowrap">
                  {p.mw || p.unsized ? (
                    <Sourced docs={p.sources}>{mw(p.mw, p.unsized)}</Sourced>
                  ) : (
                    "–"
                  )}
                </td>
                <td className="dim">
                  {site.signal_types.map(signalLabel).join(", ")}
                </td>
                <td
                  className={`mono nowrap${isNew(latest?.date) ? " accent" : ""}`}
                >
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
                    <TieEvidence site={site} />
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
        Sites{" "}
        <span className="dim">
          · owner, tenant, operator, or permit holder · click a row for the
          evidence
        </span>
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

function TieEvidence({ site }: { site: DemandSite }) {
  return (
    <div className="evidence">
      {site.ties.map((t, i) => (
        <div key={i} className="tie">
          <div>
            <span className="strong">{t.entity}</span>{" "}
            <span className="dim">is</span> {t.role}{" "}
            <span className="dim">→ belongs to</span>{" "}
            <span className="strong">{t.company}</span>{" "}
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
        Equipment supplied{" "}
        <span className="dim">
          · {view.supply.makers.join(", ")} units named in air permits, each
          counted once
        </span>
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
                <td>
                  {i === 0 ? <a href={`#/s/${site.id}`}>{site.name}</a> : ""}
                </td>
                <td>
                  {u.manufacturer} <span className="dim">{u.model ?? ""}</span>
                </td>
                <td className="dim">
                  {u.kind === "turbines" ? "turbine" : "generator"}
                </td>
                <td className="r mono">
                  <Sourced docs={[u.source]}>{num(u.count)}</Sourced>
                </td>
                <td className="r mono">
                  {u.unit_size ? `${num(u.unit_size)} ${u.unit}` : "—"}
                </td>
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
