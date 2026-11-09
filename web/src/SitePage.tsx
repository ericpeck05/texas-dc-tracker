import { useEffect, useState } from "react";
import { Conf, fetchJson, isNew, mw, num, signalLabel, statusLabel } from "./format";
import type { ChainEntity, LinkStep, SiteView } from "./types";

export function SitePage({ id }: { id: string }) {
  const [view, setView] = useState<SiteView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setView(null);
    setError(null);
    fetchJson<SiteView>(`/api/sites/${encodeURIComponent(id)}`)
      .then(setView)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  if (error)
    return (
      <main>
        <a href="#/sites" className="back">
          ← sites
        </a>
        <p className="error">{error}</p>
      </main>
    );
  if (!view) return <p className="dim pad">Loading site…</p>;

  const p = view.power;
  return (
    <main>
      <a href="#/sites" className="back">
        ← sites
      </a>
      <div className="ticker-head">
        <h1>{view.name}</h1>
        <div className="names">
          {[view.city, view.county && `${view.county} County`].filter(Boolean).join(", ") ||
            "location unknown"}
          <span className="dim"> · {statusLabel(view.status)}</span>
          {view.tceq_rns.length > 0 && (
            <span className="dim mono"> · {view.tceq_rns.join(" ")}</span>
          )}
        </div>
        {view.aliases.length > 0 && (
          <div className="dim small">Also filed as: {view.aliases.join(" · ")}</div>
        )}
      </div>

      <section className="stats">
        <Stat label="Signals" value={num(view.signals.length)} sub={view.signal_types.map(signalLabel).join(", ")} />
        <Stat label="Sources" value={num(view.sources.length)} />
        <Stat label="Generators" value={p.generators ? num(p.generators) : "–"} />
        <Stat label="Turbines" value={p.turbines ? num(p.turbines) : "–"} />
        <Stat
          label="Permitted MW"
          value={p.mw || p.unsized ? mw(p.mw, p.unsized) : "–"}
          sub={p.unsized ? `${p.unsized} units with no size` : undefined}
        />
      </section>

      {view.companies.length > 0 && (
        <section>
          <h2>
            Behind it <span className="dim">· weakest link in each chain</span>
          </h2>
          <div className="chips">
            {view.companies.map((c) => (
              <span key={c.company} className="chip">
                {c.ticker ? (
                  <a href={`#/t/${c.ticker}`} className="sym">
                    {c.ticker}
                  </a>
                ) : null}{" "}
                {c.company} <span className="dim">{c.roles.join(", ")}</span>{" "}
                <Conf level={c.confidence} />
              </span>
            ))}
          </div>
        </section>
      )}

      <OwnerChain chain={view.owner_chain} />
      <Timeline view={view} />
      <Equipment view={view} />

      {view.review.length > 0 && (
        <section>
          <h2>
            Open questions <span className="dim">· low-confidence links for review</span>
          </h2>
          {view.review.map((r, i) => (
            <div key={i} className="step">
              <Conf level={r.confidence} /> {r.note}{" "}
              {r.site && <a href={`#/s/${r.site.id}`}>{r.site.name}</a>}{" "}
              {r.evidence_url && (
                <a href={r.evidence_url} target="_blank" rel="noreferrer">
                  source ↗
                </a>
              )}
            </div>
          ))}
        </section>
      )}

      {view.status_history.length > 0 && (
        <section>
          <h2>Status history</h2>
          <table className="grid">
            <tbody>
              {view.status_history.map((h, i) => (
                <tr key={i}>
                  <td className="mono nowrap dim">{h.at ?? "—"}</td>
                  <td className="nowrap">
                    {h.from ? `${statusLabel(h.from)} → ` : ""}
                    {statusLabel(h.to)}
                  </td>
                  <td className="dim">{h.note}</td>
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

function Stat(props: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{props.label}</div>
      <div className="stat-value mono">{props.value}</div>
      {props.sub && <div className="stat-sub dim">{props.sub}</div>}
    </div>
  );
}

function Evidence({ step, label }: { step: LinkStep; label?: string }) {
  return (
    <span className="step-inline">
      <Conf level={step.confidence} /> {label && <span className="dim">{label} </span>}
      {step.note ?? step.method.replace(/_/g, " ")}{" "}
      {step.evidence_url && (
        <a href={step.evidence_url} target="_blank" rel="noreferrer">
          source ↗
        </a>
      )}
    </span>
  );
}

function OwnerChain({ chain }: { chain: ChainEntity[] }) {
  if (!chain.length) return null;
  return (
    <section>
      <h2>
        Owner chain <span className="dim">· legal entity on a filing → parent company</span>
      </h2>
      <table className="grid chain">
        <thead>
          <tr>
            <th>Entity</th>
            <th>Named on the filing as</th>
            <th>Parent company</th>
            <th>Chain</th>
          </tr>
        </thead>
        <tbody>
          {chain.map((e) => (
            <tr key={e.entity}>
              <td className="strong">{e.entity}</td>
              <td>
                {e.roles.map((r) => (
                  <div key={r.role}>
                    <span>{r.role}</span> <Evidence step={r} />
                  </div>
                ))}
              </td>
              <td>
                {e.companies.length === 0 && e.affiliates.length === 0 && (
                  <span className="dim">not traced yet</span>
                )}
                {e.companies.map((c) => (
                  <div key={c.company}>
                    {c.ticker && (
                      <a href={`#/t/${c.ticker}`} className="sym">
                        {c.ticker}
                      </a>
                    )}{" "}
                    {c.company} <Evidence step={c} />
                  </div>
                ))}
                <Affiliates items={e.affiliates} />
              </td>
              <td>{e.chain_confidence ? <Conf level={e.chain_confidence} /> : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Affiliates({ items }: { items: ChainEntity["affiliates"] }) {
  const [open, setOpen] = useState(false);
  const shown = open ? items : items.slice(0, 2);
  return (
    <>
      {shown.map((a) => (
        <div key={a.entity} className="dim">
          sister entity {a.entity} <Evidence step={a} />
        </div>
      ))}
      {items.length > 2 && (
        <button className="linkish" onClick={() => setOpen(!open)}>
          {open ? "show fewer" : `show ${items.length - 2} more sister entities`}
        </button>
      )}
    </>
  );
}

function Timeline({ view }: { view: SiteView }) {
  return (
    <section>
      <h2>
        Timeline <span className="dim">· every filing tied to this site, newest first</span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Date</th>
            <th>Signal</th>
            <th>Filing</th>
            <th>Source</th>
            <th>Tied by</th>
          </tr>
        </thead>
        <tbody>
          {view.signals.map((s) => (
            <tr key={s.id} className={isNew(s.date) ? "new" : undefined}>
              <td className="mono nowrap">{s.date ?? "undated"}</td>
              <td className="nowrap">{signalLabel(s.type)}</td>
              <td>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noreferrer">
                    {s.title ?? s.permit ?? "document"}
                  </a>
                ) : (
                  (s.title ?? s.permit)
                )}
                {s.status && <span className="dim"> · {s.status.toLowerCase()}</span>}
                {s.equipment_rows > 0 && (
                  <span className="dim"> · {s.equipment_rows} equipment rows</span>
                )}
              </td>
              <td className="dim">{s.source_label}</td>
              <td>{s.match ? <Evidence step={s.match} /> : <span className="dim">site ID</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Equipment({ view }: { view: SiteView }) {
  if (!view.equipment.length) return null;
  return (
    <section>
      <h2>
        Equipment <span className="dim">· from air permits, each unit counted once</span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Permit</th>
            <th>Type</th>
            <th>Make / model</th>
            <th className="r">Units</th>
            <th className="r">Unit size</th>
            <th className="r">MW</th>
            <th>Page</th>
          </tr>
        </thead>
        <tbody>
          {view.equipment.flatMap((permit) =>
            permit.rows.map((r, i) => (
              <tr key={`${permit.permit}-${i}`}>
                <td className="mono nowrap">
                  {i === 0 ? (
                    permit.url ? (
                      <a href={permit.url} target="_blank" rel="noreferrer">
                        {permit.permit}
                      </a>
                    ) : (
                      permit.permit
                    )
                  ) : (
                    ""
                  )}
                </td>
                <td className="dim">{r.category.replace(/_/g, " ")}</td>
                <td>
                  {r.manufacturer ?? <span className="dim">make not stated</span>}{" "}
                  <span className="dim">{r.model ?? ""}</span>
                </td>
                <td className="r mono">{num(r.count)}</td>
                <td className="r mono">{r.unit_size ? `${num(r.unit_size)} ${r.unit ?? ""}` : "—"}</td>
                <td className="r mono">{r.mw ? mw(r.mw) : "—"}</td>
                <td className="dim mono" title={r.snippet ?? ""}>
                  {r.page && permit.url ? (
                    <a href={`${permit.url}#page=${r.page}`} target="_blank" rel="noreferrer">
                      p. {r.page}
                    </a>
                  ) : (
                    (r.page ?? "")
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
