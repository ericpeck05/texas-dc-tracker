import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fetchJson, mw, num, statusLabel } from "./format";
import type { Overview, OverviewCompany, OverviewSite, PaceQuarter } from "./types";

// Single-series quarterly bars. The current quarter is not over, so it is drawn hollow.
function QuarterBars({
  rows,
  value,
  unit,
  height = 140,
  compact = false,
}: {
  rows: PaceQuarter[];
  value: (q: PaceQuarter) => number;
  unit: (n: number) => string;
  height?: number;
  compact?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const top = compact ? 14 : 18;
  const bottom = 18;
  const plot = height - top - bottom;
  const values = rows.map(value);
  const max = Math.max(1, ...values);
  const step = width / rows.length;
  const bar = Math.max(2, Math.min(28, step - 2));
  const y = (v: number) => top + plot - (v / max) * plot;
  const peak = values.indexOf(Math.max(...values));
  const h = hover !== null ? rows[hover] : null;

  return (
    <div className="qbars" ref={box}>
      <svg width={width} height={height} role="img" aria-label="Bars by quarter">
        <line
          x1={0}
          x2={width}
          y1={top + plot}
          y2={top + plot}
          className="axis"
        />
        {rows.map((q, i) => {
          const v = values[i];
          const x = i * step + (step - bar) / 2;
          const yy = y(v);
          const hgt = Math.max(v ? 2 : 0, top + plot - yy);
          const r = Math.min(4, bar / 2, hgt);
          // Rounded top, square base on the axis.
          const d = hgt
            ? `M${x},${top + plot}V${yy + r}Q${x},${yy} ${x + r},${yy}H${x + bar - r}Q${x + bar},${yy} ${x + bar},${yy + r}V${top + plot}Z`
            : "";
          const year = q.quarter.endsWith("Q1");
          return (
            <g key={q.quarter}>
              {d && (
                <path
                  d={d}
                  className={`qbar${q.partial ? " partial" : ""}${hover === i ? " hot" : ""}`}
                />
              )}
              {year && (
                <text x={x} y={height - 4} className="tick">
                  {compact ? `'${q.quarter.slice(2, 4)}` : q.quarter.slice(0, 4)}
                </text>
              )}
              {!compact && i === peak && v > 0 && hover === null && (
                <text x={x + bar / 2} y={yy - 4} className="label" textAnchor="middle">
                  {num(v)}
                </text>
              )}
              <rect
                x={i * step}
                y={0}
                width={step}
                height={height}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          );
        })}
        {compact && (
          <text x={width} y={10} className="tick" textAnchor="end">
            max {num(max)}
          </text>
        )}
      </svg>
      {h && hover !== null && (
        <div
          className="qtip"
          style={{
            left: Math.min(Math.max(hover * step + step / 2, 70), width - 70),
          }}
        >
          <span className="dim">{h.quarter}{h.partial ? " (so far)" : ""}</span>
          <br />
          {unit(value(h))}
        </div>
      )}
    </div>
  );
}

function lastFour(rows: PaceQuarter[], value: (q: PaceQuarter) => number) {
  const done = rows.filter((q) => !q.partial);
  const sum = (xs: PaceQuarter[]) => xs.reduce((a, q) => a + value(q), 0);
  return { recent: sum(done.slice(-4)), prior: sum(done.slice(-8, -4)) };
}

function Change({ recent, prior }: { recent: number; prior: number }) {
  if (!prior) return <span className="dim">none the year before</span>;
  const pct = Math.round(((recent - prior) / prior) * 100);
  return (
    <span className="dim">
      {pct >= 0 ? "+" : ""}
      {pct}% vs the 4 before ({num(prior)})
    </span>
  );
}

const KINDS: { key: keyof PaceQuarter; title: string; note: string }[] = [
  {
    key: "air_permit",
    title: "Air permits",
    note: "backup generators or turbines: a site getting ready to run",
  },
  {
    key: "stormwater",
    title: "Stormwater permits",
    note: "filed before moving dirt: construction is starting",
  },
  {
    key: "incentive",
    title: "Sales-tax exemptions",
    note: "Comptroller data center registry: a qualifying project",
  },
  {
    key: "grid",
    title: "PUC grid dockets",
    note: "large-load interconnection and rate cases",
  },
];

function SiteLinks({ sites, limit = 8 }: { sites: OverviewSite[]; limit?: number }) {
  const shown = sites.slice(0, limit);
  return (
    <>
      {shown.map((s, i) => (
        <span key={s.id}>
          {i > 0 && ", "}
          <a href={`#/s/${s.id}`}>{s.name}</a>
        </span>
      ))}
      {sites.length > limit && <span className="dim"> +{sites.length - limit} more</span>}
    </>
  );
}

function CompanyRow({
  c,
  open,
  toggle,
  most,
}: {
  c: OverviewCompany;
  open: boolean;
  toggle: () => void;
  most: number;
}) {
  const st = c.status;
  return (
    <Fragment>
      <tr className={`clickable${open ? " open" : ""}`} onClick={toggle}>
        <td className="nowrap">
          <span className="caret">{open ? "▾" : "▸"}</span> {c.company}
        </td>
        <td>
          {c.ticker ? (
            <a href={`#/t/${c.ticker}`} className="sym" onClick={(e) => e.stopPropagation()}>
              {c.ticker}
            </a>
          ) : (
            <span className="dim">private</span>
          )}
        </td>
        <td>
          <div className="share">
            <span className="mono">{num(c.sites.length)}</span>
            <span className="bar neutral" style={{ width: `${(c.sites.length / most) * 90}px` }} />
          </div>
        </td>
        <td className={`r mono${c.new_12m ? "" : " dim"}`}>{c.new_12m || "–"}</td>
        <td className="r mono">{st.under_construction ?? "–"}</td>
        <td className="r mono">{st.approved ?? "–"}</td>
        <td className="r mono dim">{st.filed ?? "–"}</td>
        <td className="r mono">{c.mw ? mw(c.mw) : "–"}</td>
        <td className="r mono dim nowrap">{c.first?.slice(0, 4) ?? "—"}</td>
        <td className="r mono nowrap">{c.latest ?? "—"}</td>
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={10}>
            <table className="mini">
              <tbody>
                {c.sites.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <a href={`#/s/${s.id}`}>{s.name}</a>
                    </td>
                    <td className="dim">{s.county ?? "—"}</td>
                    <td className="dim nowrap">{statusLabel(s.status)}</td>
                    <td className="r mono">{s.units ? `${num(s.units)} units` : ""}</td>
                    <td className="r mono">{s.mw ? `${mw(s.mw)} MW` : ""}</td>
                    <td className="r mono dim">{s.first ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [allCompanies, setAllCompanies] = useState(false);

  useEffect(() => {
    fetchJson<Overview>("/api/overview")
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  if (error) return <p className="error pad">{error}</p>;
  if (!data) return <p className="dim pad">Loading overview…</p>;

  const t = data.totals;
  const growth = t.new_prior_12m
    ? Math.round(((t.new_12m - t.new_prior_12m) / t.new_prior_12m) * 100)
    : null;
  const companies = allCompanies ? data.companies : data.companies.slice(0, 12);
  const most = Math.max(1, ...data.companies.map((c) => c.sites.length));
  const sites = lastFour(data.pace, (q) => q.new_sites);

  return (
    <main>
      <h1 className="page-title">Overview</h1>
      <p className="dim lede">
        Who is building data centers in Texas and how fast, summed up from every public filing
        the tracker has. As of {data.as_of}.
      </p>

      <div className="stats">
        <div className="stat">
          <div className="stat-label">Sites tracked</div>
          <div className="stat-value mono">{num(t.sites)}</div>
          <div className="stat-sub dim">
            {num(t.status.under_construction ?? 0)} under construction ·{" "}
            {num(t.status.approved ?? 0)} approved
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">New in last 12 months</div>
          <div className="stat-value mono">{num(t.new_12m)}</div>
          <div className="stat-sub dim">
            {growth !== null
              ? `${growth >= 0 ? "+" : ""}${growth}% vs the 12 before (${num(t.new_prior_12m)})`
              : "first filings"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Tied to a named company</div>
          <div className="stat-value mono">{num(t.named)}</div>
          <div className="stat-sub dim">
            {Math.round((t.named / Math.max(1, t.sites)) * 100)}% of sites ·{" "}
            {num(data.companies.length)} companies
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Generators + turbines</div>
          <div className="stat-value mono">{num(t.units)}</div>
          <div className="stat-sub dim">in air permits at {num(t.unit_sites)} sites</div>
        </div>
        <div className="stat">
          <div className="stat-label">Sized on-site power</div>
          <div className="stat-value mono">{mw(t.mw)} MW</div>
          <div className="stat-sub dim">only {num(t.powered_sites)} sites give unit sizes</div>
        </div>
      </div>

      <h2>What stands out</h2>
      <ul className="takeaways">
        {data.takeaways.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      <h2>
        Pace <span className="dim">· new sites by the quarter of their first public filing</span>
      </h2>
      <div className="pace-head">
        <span className="mono">{num(sites.recent)}</span>{" "}
        <span className="dim">new sites in the last 4 full quarters</span> ·{" "}
        <Change {...sites} />
      </div>
      <QuarterBars
        rows={data.pace}
        value={(q) => q.new_sites}
        unit={(n) => `${num(n)} new site${n === 1 ? "" : "s"}`}
        height={180}
      />

      <h2>
        Filings by kind <span className="dim">· each is a stage of a build; own scale per chart</span>
      </h2>
      <div className="multiples">
        {KINDS.map((k) => {
          const v = (q: PaceQuarter) => Number(q[k.key]) || 0;
          const c = lastFour(data.pace, v);
          return (
            <div key={k.key} className="multiple">
              <div className="multiple-title">{k.title}</div>
              <div className="dim small">{k.note}</div>
              <QuarterBars
                rows={data.pace}
                value={v}
                unit={(n) => `${num(n)} filing${n === 1 ? "" : "s"}`}
                height={90}
                compact
              />
              <div className="small">
                <span className="mono">{num(c.recent)}</span>{" "}
                <span className="dim">last 4 quarters ·</span> <Change {...c} />
              </div>
            </div>
          );
        })}
        <div className="multiple">
          <div className="multiple-title">Generators + turbines</div>
          <div className="dim small">units in air permits, by the permit's quarter</div>
          <QuarterBars
            rows={data.pace}
            value={(q) => q.units}
            unit={(n) => `${num(n)} units`}
            height={90}
            compact
          />
          <div className="small">
            <span className="mono">{num(lastFour(data.pace, (q) => q.units).recent)}</span>{" "}
            <span className="dim">last 4 quarters ·</span>{" "}
            <Change {...lastFour(data.pace, (q) => q.units)} />
          </div>
        </div>
      </div>
      <p className="dim small">
        Hover a bar for its count. The current quarter is hollow because it is not over. Years
        before 2021 are left out; sources reach back unevenly, so older years look quieter than
        they were.
      </p>

      <h2>
        Who is building{" "}
        <span className="dim">· companies tied to sites at medium or high confidence</span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Company</th>
            <th>Ticker</th>
            <th>Sites</th>
            <th className="r">New 12 mo</th>
            <th className="r">Building</th>
            <th className="r">Approved</th>
            <th className="r">Filed</th>
            <th className="r">Sized MW</th>
            <th className="r">Since</th>
            <th className="r">Latest filing</th>
          </tr>
        </thead>
        <tbody>
          {companies.map((c) => (
            <CompanyRow
              key={c.company}
              c={c}
              most={most}
              open={open === c.company}
              toggle={() => setOpen(open === c.company ? null : c.company)}
            />
          ))}
        </tbody>
      </table>
      {data.companies.length > 12 && (
        <button className="linkish" onClick={() => setAllCompanies(!allCompanies)}>
          {allCompanies ? "Show top 12" : `Show all ${data.companies.length} companies`}
        </button>
      )}
      <p className="dim small">
        Click a company for its sites. A site with several companies (a developer and its
        tenant) counts for each. "Building" and "Approved" come from the latest status the
        filings show. Sized MW counts only units whose size a permit gives.
      </p>

      {data.other_names.length > 0 && (
        <>
          <h2>
            Other names on filings{" "}
            <span className="dim">
              · owners, operators and tenants not yet traced to a parent company
            </span>
          </h2>
          <ul className="names-list">
            {data.other_names.slice(0, 24).map((o) => (
              <li key={o.entity}>
                <span>{o.entity}</span>{" "}
                <span className="dim small">
                  <SiteLinks sites={o.sites} limit={3} />
                </span>
              </li>
            ))}
          </ul>
          {data.other_names.length > 24 && (
            <p className="dim small">+{data.other_names.length - 24} more on the site pages.</p>
          )}
        </>
      )}

      <div className="two-col">
        <section>
          <h2>Where</h2>
          <table className="grid">
            <thead>
              <tr>
                <th>County</th>
                <th className="r">Sites</th>
                <th className="r">New 12 mo</th>
                <th className="r">Sized MW</th>
              </tr>
            </thead>
            <tbody>
              {data.counties.slice(0, 12).map((c) => (
                <tr key={c.county}>
                  <td>{c.county}</td>
                  <td className="r mono">{num(c.sites)}</td>
                  <td className={`r mono${c.new_12m ? "" : " dim"}`}>{c.new_12m || "–"}</td>
                  <td className="r mono">{c.mw ? mw(c.mw) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="dim small">
            {num(t.sites - data.counties.reduce((a, c) => a + c.sites, 0))} sites have no county
            yet. <a href="#/sites">Map + sites</a> has every site.
          </p>
        </section>
        <section>
          <h2>Newest sites</h2>
          <table className="grid">
            <thead>
              <tr>
                <th>Site</th>
                <th>County</th>
                <th className="r">First filing</th>
              </tr>
            </thead>
            <tbody>
              {data.newest.map((s) => (
                <tr key={s.id}>
                  <td>
                    <a href={`#/s/${s.id}`}>{s.name}</a>
                  </td>
                  <td className="dim">{s.county ?? "—"}</td>
                  <td className="r mono nowrap">{s.first}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="dim small">
            <a href="#/feed">The feed</a> has every filing, newest first.
          </p>
        </section>
      </div>

      <footer className="disclaimer">{data.disclaimer}</footer>
    </main>
  );
}
