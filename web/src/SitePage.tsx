import { useEffect, useState } from "react";
import { Conf, Inferred, fetchJson, isNew, mw, num, signalLabel, statusLabel } from "./format";
import type { ChainEntity, LinkStep, SiteView } from "./types";
import {
  Companies,
  Evidence,
  KindTag,
  StageBar,
  StatusChip,
  ago,
  longDate,
  placePhrase,
  powerPhrase,
} from "./ui";

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
          ← Map
        </a>
        <p className="error">{error}</p>
      </main>
    );
  if (!view) return <p className="dim pad">Loading site…</p>;

  const p = view.power;
  const dated = view.signals.filter((x) => x.date).map((x) => x.date!) ;
  const first = dated.length ? dated.reduce((a, b) => (a < b ? a : b)) : null;
  const latest = dated.length ? dated.reduce((a, b) => (a > b ? a : b)) : null;
  const filers = view.owner_chain
    .filter((e) => !e.companies.length)
    .map((e) => e.entity)
    .slice(0, 3);
  const power = powerPhrase(p);
  return (
    <main>
      <a href="#/sites" className="back">
        ← Map
      </a>
      <div className="page-head">
        <h1 className="page-title">{view.name}</h1>
        <StatusChip status={view.status} />
      </div>
      <p className="page-sub">{placePhrase(view.county, view.city)}</p>
      {view.merged_into && (
        <p className="done">
          Merged in review into{" "}
          <a href={`#/s/${view.merged_into.id}`}>{view.merged_into.name}</a>.
        </p>
      )}
      <StageBar status={view.status} />

      <p className="lede-big">
        {summary(view, power, filers)}{" "}
        {first && (
          <>
            {num(view.signals.length)} public filing{view.signals.length === 1 ? "" : "s"} so
            far, the first on {longDate(first)}
            {latest && latest !== first ? ` and the latest ${ago(latest)}` : ""}.
          </>
        )}
      </p>

      <div className="cards">
        <div className="card">
          <h3>Who is behind it</h3>
          <Companies items={view.companies} />
          {filers.length > 0 && (
            <p className="small dim" style={{ marginBottom: 0 }}>
              Filed under {filers.join(", ")}
              {view.companies.length ? "" : "; not yet traced to a parent company"}.
            </p>
          )}
        </div>
        <div className="card">
          <h3>Power on site</h3>
          {power ? (
            <>
              <div className="big-figure">{power}</div>
              <Makers view={view} />
            </>
          ) : (
            <span className="dim">No air permit counts generators or turbines here yet.</span>
          )}
        </div>
      </div>

      <section className="card">
        <h3>Timeline</h3>
        <PlainTimeline view={view} />
      </section>

      <Evidence
        title="Show the evidence"
        hint="legal entities, permit equipment, how each filing was matched"
      >
        {view.aliases.length > 0 && (
          <p className="dim small">Also filed as: {view.aliases.join(" · ")}</p>
        )}
        {view.tceq_rns.length > 0 && (
          <p className="dim small mono">TCEQ site IDs: {view.tceq_rns.join(" ")}</p>
        )}
        <OwnerChain chain={view.owner_chain} />
        <Equipment view={view} />
        <Timeline view={view} />
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
      </Evidence>

      <footer className="disclaimer">{view.disclaimer}</footer>
    </main>
  );
}

const STAGE_PHRASE: Record<string, string> = {
  rumored: "has been announced but has no permits on file yet",
  filed: "is at the permit stage",
  approved: "has been approved",
  under_construction: "is under construction",
  operating: "is operating",
  cancelled: "was cancelled",
};

function summary(view: SiteView, power: string | null, filers: string[]): string {
  const where = view.county ? ` in ${placePhrase(view.county, view.city)}` : "";
  const parts = [`This data center project${where} ${STAGE_PHRASE[view.status] ?? ""}.`];
  const named = view.companies.filter((c) => c.confidence !== "low");
  if (named.length) {
    const who = named
      .slice(0, 3)
      .map((c) => `${c.company}${c.roles.length ? ` (${c.roles.join(", ")})` : ""}`);
    parts.push(
      `${who.join(who.length > 2 ? ", " : " and ")} ${named.length > 1 ? "are" : "is"} behind it.`,
    );
  } else if (filers.length) {
    parts.push(`It is filed under ${filers[0]}, whose parent company isn't traced yet.`);
  }
  if (power) parts.push(`Air permits list ${power.replace(" · ", ", about ")} of on-site power.`);
  return parts.join(" ");
}

function Makers({ view }: { view: SiteView }) {
  const seen = new Map<string, { make: string; model: string | null; url: string | null }>();
  for (const permit of view.equipment)
    for (const r of permit.rows) {
      if (!r.kind) continue;
      const make = r.manufacturer ?? "Make not stated";
      const key = `${make}|${r.model ?? ""}`;
      if (!seen.has(key)) seen.set(key, { make, model: r.model, url: permit.url });
    }
  if (!seen.size) return null;
  return (
    <p className="small" style={{ marginBottom: 0 }}>
      <span className="dim">Equipment named: </span>
      {[...seen.values()].map((m, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {m.url ? (
            <a href={m.url} target="_blank" rel="noreferrer">
              {m.make}
              {m.model ? ` ${m.model}` : ""}
            </a>
          ) : (
            `${m.make}${m.model ? ` ${m.model}` : ""}`
          )}
        </span>
      ))}
    </p>
  );
}

function PlainTimeline({ view }: { view: SiteView }) {
  const [all, setAll] = useState(false);
  const items = all ? view.signals : view.signals.slice(0, 6);
  if (!items.length) return <span className="dim">No filings yet.</span>;
  return (
    <>
      <ol className="timeline">
        {items.map((s) => (
          <li key={s.id} className={isNew(s.date) ? "new" : undefined}>
            <div className="tl-date">
              {s.date ? longDate(s.date) : "Undated"} <KindTag type={s.type} />
            </div>
            <div className="tl-title">
              {s.url ? (
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.title ?? s.permit ?? "Document"}
                </a>
              ) : (
                (s.title ?? s.permit)
              )}
              <span className="dim small"> · {s.source_label}</span>
            </div>
          </li>
        ))}
      </ol>
      {view.signals.length > 6 && (
        <button className="linkish" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${view.signals.length} filings`}
        </button>
      )}
    </>
  );
}

function StepNote({ step, label }: { step: LinkStep; label?: string }) {
  return (
    <span className="step-inline">
      <Conf level={step.confidence} />{" "}
      {label && <span className="dim">{label} </span>}
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
        Owner chain{" "}
        <span className="dim">· legal entity on a filing → parent company</span>
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
                    <span>{r.role}</span> <StepNote step={r} />
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
                    {c.company} <StepNote step={c} />
                  </div>
                ))}
                <Affiliates items={e.affiliates} />
              </td>
              <td>
                {e.chain_confidence ? <Conf level={e.chain_confidence} /> : "–"}
              </td>
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
          sister entity {a.entity} <StepNote step={a} />
        </div>
      ))}
      {items.length > 2 && (
        <button className="linkish" onClick={() => setOpen(!open)}>
          {open
            ? "show fewer"
            : `show ${items.length - 2} more sister entities`}
        </button>
      )}
    </>
  );
}

function Timeline({ view }: { view: SiteView }) {
  return (
    <section>
      <h2>
        Timeline{" "}
        <span className="dim">
          · every filing tied to this site, newest first
        </span>
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
                {s.status && (
                  <span className="dim"> · {s.status.toLowerCase()}</span>
                )}
                {s.equipment_rows > 0 && (
                  <span className="dim">
                    {" "}
                    · {s.equipment_rows} equipment rows
                  </span>
                )}
              </td>
              <td className="dim">{s.source_label}</td>
              <td>
                {s.match ? (
                  <StepNote step={s.match} />
                ) : (
                  <span className="dim">site ID</span>
                )}
              </td>
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
        Equipment{" "}
        <span className="dim">· from air permits, each unit counted once</span>
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
                  {r.manufacturer ?? (
                    <span className="dim">make not stated</span>
                  )}{" "}
                  <span className="dim">{r.model ?? ""}</span>{" "}
                  <Inferred source={r.make_source} />
                </td>
                <td className="r mono">{num(r.count)}</td>
                <td className="r mono">
                  {r.unit_size ? `${num(r.unit_size)} ${r.unit ?? ""}` : "—"}
                </td>
                <td className="r mono">{r.mw ? mw(r.mw) : "—"}</td>
                <td className="dim mono" title={r.snippet ?? ""}>
                  {r.page && permit.url ? (
                    <a
                      href={`${permit.url}#page=${r.page}`}
                      target="_blank"
                      rel="noreferrer"
                    >
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
