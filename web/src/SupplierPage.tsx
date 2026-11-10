import { Fragment, useEffect, useState } from "react";
import { fetchJson, mw, num, statusLabel } from "./format";
import type { MakerRow, Scoreboard } from "./types";

type Kind = "all" | "generators" | "turbines";
type Metric = "units" | "mw";

function units(
  m: { generators: number; turbines: number },
  kind: Kind,
): number {
  return kind === "generators"
    ? m.generators
    : kind === "turbines"
      ? m.turbines
      : m.generators + m.turbines;
}

export function SupplierPage() {
  const [data, setData] = useState<Scoreboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>("all");
  const [metric, setMetric] = useState<Metric>("mw");
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    fetchJson<Scoreboard>("/api/suppliers")
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  if (error)
    return <p className="error pad">Could not load the scoreboard: {error}</p>;
  if (!data) return <p className="dim pad">Loading scoreboard…</p>;

  // MW is per row, so a kind filter on MW uses the rows of that kind.
  const value = (m: MakerRow) =>
    metric === "units"
      ? units(m, kind)
      : kind === "all"
        ? m.mw
        : m.rows.filter((r) => r.kind === kind).reduce((s, r) => s + r.mw, 0);
  const qValue = (
    q: { generators: number; turbines: number; mw: number } | undefined,
  ) =>
    !q ? 0 : metric === "units" ? units(q, kind) : kind === "all" ? q.mw : null;

  const makers = data.makers
    .filter((m) => units(m, kind) !== 0)
    .sort((a, b) => Number(!a.known) - Number(!b.known) || value(b) - value(a));
  const knownTotal = makers
    .filter((m) => m.known)
    .reduce((s, m) => s + value(m), 0);
  const unknown = makers.find((m) => !m.known);
  const allTotal = makers.reduce((s, m) => s + value(m), 0);
  const fmt = (v: number) => (metric === "units" ? num(v) : mw(v));
  const quarters = data.quarters;

  return (
    <main>
      <h1 className="page-title">Supplier scoreboard</h1>
      <p className="dim lede">
        Generators and turbines in Texas data center air permits, by
        manufacturer. Each unit counted once per site, as on the site and ticker
        pages. A unit's quarter is when its permit was first filed. Share is of
        units whose make the permit names:{" "}
        {unknown && (
          <>
            {num(units(unknown, kind))} of{" "}
            {num(makers.reduce((s, m) => s + units(m, kind), 0))} permitted
            units name no manufacturer, so read shares as a sample.
          </>
        )}
      </p>
      <div className="filters">
        <div className="tabs inline">
          {(["all", "generators", "turbines"] as Kind[]).map((k) => (
            <button
              key={k}
              className={k === kind ? "tab here" : "tab"}
              onClick={() => setKind(k)}
            >
              {k === "all" ? "All units" : k[0].toUpperCase() + k.slice(1)}
            </button>
          ))}
        </div>
        <div className="tabs inline">
          {(["mw", "units"] as Metric[]).map((m) => (
            <button
              key={m}
              className={m === metric ? "tab here" : "tab"}
              onClick={() => setMetric(m)}
            >
              {m === "mw" ? "By MW" : "By unit count"}
            </button>
          ))}
        </div>
      </div>
      <div className="table-side">
        <table className="grid scoreboard">
          <thead>
            <tr>
              <th>#</th>
              <th>Manufacturer</th>
              <th className="r">{metric === "mw" ? "MW" : "Units"}</th>
              <th>Share of named</th>
              <th className="r">Gen.</th>
              <th className="r">Turb.</th>
              <th className="r">Sites</th>
              {quarters.map((q) => (
                <th key={q} className="r nowrap">
                  {q.replace("-", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {makers.map((m, i) => {
              const v = value(m);
              const share = m.known && knownTotal ? v / knownTotal : null;
              return (
                <Fragment key={m.maker}>
                  <tr
                    className={`clickable${m.known ? "" : " unknown"}${open === m.maker ? " open" : ""}`}
                    onClick={() => setOpen(open === m.maker ? null : m.maker)}
                  >
                    <td className="dim mono">{m.known ? i + 1 : ""}</td>
                    <td className="nowrap">
                      <span className="dim">
                        {open === m.maker ? "▾" : "▸"}
                      </span>{" "}
                      <span className={m.known ? "strong" : "dim"}>
                        {m.maker}
                      </span>{" "}
                      {m.ticker && (
                        <a
                          href={`#/t/${m.ticker}`}
                          className="sym"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {m.ticker}
                        </a>
                      )}
                      {m.parent && (
                        <span className="dim small"> · {m.parent}</span>
                      )}
                    </td>
                    <td className="r mono">{fmt(v)}</td>
                    <td>
                      {share != null && (
                        <span className="share">
                          <span
                            className="bar"
                            style={{ width: `${Math.max(share * 100, 1)}%` }}
                          />
                          <span className="mono small">
                            {Math.round(share * 100)}%
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="r mono">
                      {m.generators ? num(m.generators) : "–"}
                    </td>
                    <td className="r mono">
                      {m.turbines ? num(m.turbines) : "–"}
                    </td>
                    <td className="r mono">{num(m.sites)}</td>
                    {quarters.map((q) => {
                      const qv = qValue(m.quarters[q]);
                      return (
                        <td key={q} className="r mono dim">
                          {qv == null ? "·" : qv ? fmt(qv) : ""}
                        </td>
                      );
                    })}
                  </tr>
                  {open === m.maker && (
                    <tr className="detail">
                      <td />
                      <td colSpan={6 + quarters.length}>
                        <MakerDetail m={m} kind={kind} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td />
              <td className="dim">All permitted units</td>
              <td className="r mono">{fmt(allTotal)}</td>
              <td />
              <td className="r mono">{num(data.totals.generators)}</td>
              <td className="r mono">{num(data.totals.turbines)}</td>
              <td colSpan={1 + quarters.length} />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="dim small">
        MW counts only units with a stated size; {num(data.totals.unsized)}{" "}
        units have none, so MW is a floor.{" "}
        {metric === "mw" &&
          kind !== "all" &&
          "Quarter columns show MW only for all units. "}
        Click a manufacturer for the models, sites, and the permit page each
        count comes from.
      </p>
      <footer className="disclaimer">{data.disclaimer}</footer>
    </main>
  );
}

function MakerDetail({ m, kind }: { m: MakerRow; kind: Kind }) {
  const rows = m.rows.filter((r) => kind === "all" || r.kind === kind);
  return (
    <div className="maker-detail">
      <div className="small">
        <span className="dim">Models: </span>
        {m.models
          .filter((x) => kind === "all" || x.kind === kind)
          .map(
            (x) =>
              `${x.model} (${num(x.count)}${x.mw ? `, ${mw(x.mw)} MW` : ""})`,
          )
          .join(" · ")}
      </div>
      <table className="grid">
        <thead>
          <tr>
            <th>First filed</th>
            <th>Site</th>
            <th>Model</th>
            <th className="r">Units</th>
            <th className="r">Size</th>
            <th className="r">MW</th>
            <th>Permit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="mono nowrap dim">{r.first_filed ?? "undated"}</td>
              <td>
                <a href={`#/s/${r.site.id}`}>{r.site.name}</a>{" "}
                <span className="dim small">{statusLabel(r.site.status)}</span>
              </td>
              <td className="dim">{r.model ?? "not stated"}</td>
              <td className="r mono">{num(r.count)}</td>
              <td className="r mono">
                {r.unit_size ? `${num(r.unit_size)} ${r.unit ?? ""}` : "—"}
              </td>
              <td className="r mono">{r.mw ? mw(r.mw) : "—"}</td>
              <td className="mono nowrap" title={r.source.snippet ?? ""}>
                {r.source.url ? (
                  <a
                    href={
                      r.source.page
                        ? `${r.source.url}#page=${r.source.page}`
                        : r.source.url
                    }
                    target="_blank"
                    rel="noreferrer"
                  >
                    {r.source.permit ?? "document"}
                    {r.source.page ? ` p.${r.source.page}` : ""}
                  </a>
                ) : (
                  (r.source.permit ?? "—")
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
