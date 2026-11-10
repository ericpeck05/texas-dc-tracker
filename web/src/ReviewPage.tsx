import { useCallback, useEffect, useRef, useState } from "react";
import { Conf, fetchJson, num, signalLabel, statusLabel } from "./format";
import type {
  ReviewItem,
  ReviewKind,
  ReviewQueue,
  ReviewRecord,
} from "./types";

const KINDS: { kind: ReviewKind; label: string; confirm: string }[] = [
  {
    kind: "same_site",
    label: "Same site?",
    confirm: "Confirm merges the two sites into one.",
  },
  {
    kind: "filing",
    label: "Filing → site",
    confirm: "Confirm moves the filing to this site.",
  },
  {
    kind: "parent",
    label: "Parent company",
    confirm: "Confirm ties the entity to the company.",
  },
  {
    kind: "affiliate",
    label: "Sister LLCs",
    confirm: "Confirm marks them as sister entities.",
  },
];

type Done = { text: string; site?: number };

export function ReviewPage() {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [kind, setKind] = useState<ReviewKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const cards = useRef<(HTMLDivElement | null)[]>([]);

  const load = useCallback((k: ReviewKind | null) => {
    const url = k ? `/api/review?kind=${k}` : "/api/review";
    return fetchJson<ReviewQueue>(url)
      .then((q) => {
        setQueue(q);
        if (!k) {
          const first =
            KINDS.find((x) => q.counts[x.kind] > 0)?.kind ?? "same_site";
          setKind(first);
          return fetchJson<ReviewQueue>(`/api/review?kind=${first}`).then(
            setQueue,
          );
        }
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load(null);
  }, [load]);

  const items = queue?.items ?? [];
  const item: ReviewItem | undefined =
    items[Math.min(current, items.length - 1)];

  const choose = (k: ReviewKind) => {
    setKind(k);
    setCurrent(0);
    setNote("");
    load(k);
  };

  const decide = useCallback(
    async (decision: "confirm" | "reject") => {
      if (!item || busy) return;
      setBusy(true);
      setError(null);
      try {
        const r = await fetch(`/api/review/${item.link_id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, note: note.trim() || null }),
        });
        const body = await r.json();
        if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`);
        setDone({
          text: `${decision === "confirm" ? "Confirmed" : "Rejected"}: ${name(item.left)} ↔ ${name(item.right)}`,
          site: body.site_id,
        });
        setNote("");
        await load(kind);
      } catch (e) {
        setError(String((e as Error).message ?? e));
      } finally {
        setBusy(false);
      }
    },
    [item, busy, note, kind, load],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement).tagName === "INPUT";
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "ArrowDown")
        setCurrent((c) => Math.min(c + 1, items.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp")
        setCurrent((c) => Math.max(c - 1, 0));
      else if (e.key === "y") decide("confirm");
      else if (e.key === "n") decide("reject");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items.length, decide]);

  useEffect(() => {
    cards.current[current]?.scrollIntoView({ block: "nearest" });
  }, [current]);

  if (!queue)
    return error ? (
      <p className="error pad">{error}</p>
    ) : (
      <p className="dim pad">Loading queue…</p>
    );
  const total = KINDS.reduce((n, k) => n + queue.counts[k.kind], 0);
  const meta = KINDS.find((k) => k.kind === kind)!;

  return (
    <main>
      <h1 className="page-title">Review queue</h1>
      <p className="dim lede">
        {num(total)} low-confidence links the importers could not settle.
        Confirm adds a manual, high-confidence link; reject hides the guess
        everywhere. Every decision is saved to{" "}
        <span className="mono">seeds/review_decisions.csv</span> and replayed on
        rebuild.{" "}
        {queue.decided.confirm + queue.decided.reject > 0 &&
          `${num(queue.decided.confirm)} confirmed, ${num(queue.decided.reject)} rejected so far.`}
      </p>
      <div className="tabs">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            className={k.kind === kind ? "tab here" : "tab"}
            onClick={() => choose(k.kind)}
          >
            {k.label}{" "}
            <span className="mono dim">{num(queue.counts[k.kind])}</span>
          </button>
        ))}
      </div>
      <p className="dim small keys">
        <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>y</kbd> confirm · <kbd>n</kbd>{" "}
        reject · {meta.confirm} Notes go in the public repo: companies only, no
        personal names.
      </p>
      {done && (
        <p className="done">
          {done.text}
          {done.site && (
            <>
              {" "}
              · <a href={`#/s/${done.site}`}>open site</a>
            </>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {items.length === 0 && (
        <p className="dim pad">Nothing left to review here.</p>
      )}
      {items.map((it, i) => (
        <div
          key={it.link_id}
          ref={(el) => (cards.current[i] = el)}
          className={`review-card${i === current ? " current" : ""}`}
          onClick={() => setCurrent(i)}
        >
          <div className="review-why">
            <Conf level="low" /> {it.note ?? it.method.replace(/_/g, " ")}{" "}
            <span className="dim">· {it.method.replace(/_/g, " ")}</span>{" "}
            {it.evidence_url && (
              <a href={it.evidence_url} target="_blank" rel="noreferrer">
                source ↗
              </a>
            )}
          </div>
          <div className="review-sides">
            <Record r={it.left} />
            <div className="review-vs dim">
              {it.kind === "parent" ? "parent of?" : "same as?"}
            </div>
            <Record r={it.right} />
          </div>
          {i === current && (
            <div className="review-actions">
              <input
                type="text"
                placeholder="Note (optional): why, with a source if you have one"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) =>
                  e.key === "Escape" && (e.target as HTMLInputElement).blur()
                }
              />
              <button
                className="btn confirm"
                disabled={busy}
                onClick={() => decide("confirm")}
              >
                Confirm <kbd>y</kbd>
              </button>
              <button
                className="btn reject"
                disabled={busy}
                onClick={() => decide("reject")}
              >
                Reject <kbd>n</kbd>
              </button>
            </div>
          )}
        </div>
      ))}
      {items.length < queue.counts[meta.kind] && (
        <p className="dim small">
          Showing the first {num(items.length)} of{" "}
          {num(queue.counts[meta.kind])}.
        </p>
      )}
    </main>
  );
}

function name(r: ReviewRecord): string {
  return r.kind === "signal" ? (r.title ?? "filing") : r.name;
}

function Record({ r }: { r: ReviewRecord }) {
  if (r.kind === "site")
    return (
      <div className="review-side">
        <div className="dim small">site</div>
        <a
          href={`#/s/${r.id}`}
          className="strong"
          onClick={(e) => e.stopPropagation()}
        >
          {r.name}
        </a>
        <div className="dim">
          {[r.city, r.county ? `${r.county} County` : "county unknown"]
            .filter(Boolean)
            .join(", ")}{" "}
          · {statusLabel(r.status)}
        </div>
        <div className="dim">
          {r.signal_count} signal{r.signal_count === 1 ? "" : "s"}:{" "}
          {r.signal_types.map(signalLabel).join(", ")}
          {r.latest_date && ` · latest ${r.latest_date}`}
        </div>
        {r.tceq_rns.length > 0 && (
          <div className="dim mono small">{r.tceq_rns.join(" ")}</div>
        )}
        {r.aliases.length > 0 && (
          <div className="dim small">also: {r.aliases.join(" · ")}</div>
        )}
        {r.companies.length > 0 && (
          <div>
            {r.companies.map((c) => (
              <span key={c.company} className="nowrap">
                {c.ticker ? (
                  <span className="sym">{c.ticker}</span>
                ) : (
                  c.company
                )}{" "}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  if (r.kind === "signal")
    return (
      <div className="review-side">
        <div className="dim small">filing · {r.source_label}</div>
        {r.url ? (
          <a
            href={r.url}
            target="_blank"
            rel="noreferrer"
            className="strong"
            onClick={(e) => e.stopPropagation()}
          >
            {r.title ?? "document"}
          </a>
        ) : (
          <span className="strong">{r.title}</span>
        )}
        <div className="dim">
          {signalLabel(r.type)} · {r.date ?? "undated"}
        </div>
        <div className="dim">
          {r.site ? (
            <>
              now on <a href={`#/s/${r.site.id}`}>{r.site.name}</a>
            </>
          ) : (
            "not on any site yet"
          )}
        </div>
      </div>
    );
  if (r.kind === "entity")
    return (
      <div className="review-side">
        <div className="dim small">legal entity</div>
        <span className="strong">{r.name}</span>
        {r.sites.length > 0 ? (
          <div className="dim">
            {r.sites.slice(0, 4).map((s, i) => (
              <span key={s.id}>
                {i > 0 && ", "}
                {s.role} of{" "}
                <a href={`#/s/${s.id}`} onClick={(e) => e.stopPropagation()}>
                  {s.name}
                </a>
              </span>
            ))}
            {r.sites.length > 4 && ` +${r.sites.length - 4} more`}
          </div>
        ) : (
          <div className="dim">on no site directly</div>
        )}
        {r.companies.length > 0 && (
          <div className="dim">
            {r.companies.map((c) => (
              <span key={c.company}>
                {c.company} <Conf level={c.confidence} />{" "}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  return (
    <div className="review-side">
      <div className="dim small">company · {r.role.replace(/_/g, " ")}</div>
      <span className="strong">{r.name}</span>
      {r.ticker && (
        <div>
          <a
            href={`#/t/${r.ticker}`}
            className="sym"
            onClick={(e) => e.stopPropagation()}
          >
            {r.ticker}
          </a>
        </div>
      )}
    </div>
  );
}
