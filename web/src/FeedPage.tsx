import { useEffect, useRef, useState } from "react";
import { fetchJson, isNew, mw, num, signalLabel, statusLabel } from "./format";
import type { FeedResponse } from "./types";

const WINDOWS: { days: number; label: string; key: string }[] = [
  { days: 7, label: "7 days", key: "1" },
  { days: 30, label: "30 days", key: "2" },
  { days: 90, label: "90 days", key: "3" },
  { days: 365, label: "1 year", key: "4" },
  { days: 0, label: "All", key: "5" },
];
const TYPES = [
  "air_permit",
  "stormwater",
  "incentive",
  "approval",
  "grid",
  "sec_mention",
  "notice",
];

export function FeedPage() {
  const [days, setDays] = useState(30);
  const [type, setType] = useState("");
  const [data, setData] = useState<FeedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);

  useEffect(() => {
    setError(null);
    const q = `/api/feed?days=${days}${type ? `&type=${type}` : ""}`;
    fetchJson<FeedResponse>(q)
      .then((d) => {
        setData(d);
        setCurrent(0);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [days, type]);

  const items = data?.items ?? [];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (
        tag === "INPUT" ||
        tag === "SELECT" ||
        e.metaKey ||
        e.ctrlKey ||
        e.altKey
      )
        return;
      const w = WINDOWS.find((x) => x.key === e.key);
      if (w) setDays(w.days);
      else if (e.key === "j" || e.key === "ArrowDown")
        setCurrent((c) => Math.min(c + 1, items.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp")
        setCurrent((c) => Math.max(c - 1, 0));
      else if ((e.key === "Enter" || e.key === "o") && items[current]?.site)
        window.location.hash = `#/s/${items[current].site!.id}`;
      else if (e.key === "d" && items[current]?.url)
        window.open(items[current].url!, "_blank");
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, current]);

  useEffect(() => {
    rows.current[current]?.scrollIntoView({ block: "nearest" });
  }, [current]);

  if (error)
    return <p className="error pad">Could not load the feed: {error}</p>;

  return (
    <main>
      <h1 className="page-title">Feed</h1>
      <p className="dim lede">
        Filings newest first, by the date on the document. Amber rows are from
        the last 30 days. Equipment is what that one filing lists.
        {data?.latest_filing && ` Latest filing: ${data.latest_filing}.`}
      </p>
      <div className="filters">
        <div className="tabs inline">
          {WINDOWS.map((w) => (
            <button
              key={w.days}
              className={w.days === days ? "tab here" : "tab"}
              onClick={() => setDays(w.days)}
              title={`key ${w.key}`}
            >
              {w.label}
            </button>
          ))}
        </div>
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All signal types</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {signalLabel(t)}
            </option>
          ))}
        </select>
        <span className="dim mono">
          {data ? `${num(items.length)} filings` : "loading…"}
        </span>
      </div>
      <p className="dim small keys">
        <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>enter</kbd> open site ·{" "}
        <kbd>d</kbd> open document · <kbd>1</kbd>–<kbd>5</kbd> time window
      </p>
      {data && items.length === 0 && (
        <p className="dim pad">
          No filings in this window. Try a longer one (<kbd>3</kbd> or{" "}
          <kbd>4</kbd>).
        </p>
      )}
      {items.length > 0 && (
        <table className="grid feed">
          <thead>
            <tr>
              <th>Date</th>
              <th>Type</th>
              <th>Filing</th>
              <th>Site</th>
              <th>Companies</th>
              <th className="r">Gen. / turb.</th>
              <th className="r">MW</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr
                key={it.id}
                ref={(el) => (rows.current[i] = el)}
                className={`${isNew(it.date) ? "new" : ""}${i === current ? " current" : ""}`}
                onClick={() => setCurrent(i)}
              >
                <td className={`mono nowrap${isNew(it.date) ? " accent" : ""}`}>
                  {it.date}
                </td>
                <td className="nowrap">
                  <span className="tag">{signalLabel(it.type)}</span>
                </td>
                <td>
                  {it.url ? (
                    <a href={it.url} target="_blank" rel="noreferrer">
                      {it.title ?? it.permit ?? "document"}
                    </a>
                  ) : (
                    (it.title ?? it.permit)
                  )}
                  {it.status && (
                    <span className="dim"> · {it.status.toLowerCase()}</span>
                  )}
                  {it.revised && <span className="accent"> · revised</span>}
                  <div className="dim small">{it.source_label}</div>
                </td>
                <td>
                  {it.site ? (
                    <>
                      <a href={`#/s/${it.site.id}`}>{it.site.name}</a>
                      <div className="dim small">
                        {it.site.county
                          ? `${it.site.county} County`
                          : "county unknown"}{" "}
                        · {statusLabel(it.site.status)}
                      </div>
                    </>
                  ) : (
                    <a href="#/review" className="dim">
                      no site yet: review
                    </a>
                  )}
                </td>
                <td>
                  {it.companies.map((c, j) => (
                    <span key={c.company} className="nowrap">
                      {j > 0 && ", "}
                      {c.ticker ? (
                        <a href={`#/t/${c.ticker}`} className="sym">
                          {c.ticker}
                        </a>
                      ) : (
                        c.company
                      )}
                      {c.confidence === "medium" && (
                        <span className="dim">?</span>
                      )}
                    </span>
                  ))}
                </td>
                <td className="r mono">
                  {it.equipment.generators || it.equipment.turbines
                    ? `${num(it.equipment.generators)} / ${num(it.equipment.turbines)}`
                    : "–"}
                </td>
                <td className="r mono">
                  {it.equipment.mw && it.url ? (
                    <a
                      href={it.url}
                      target="_blank"
                      rel="noreferrer"
                      className="num-link"
                    >
                      {mw(it.equipment.mw)}
                    </a>
                  ) : it.equipment.mw ? (
                    mw(it.equipment.mw)
                  ) : (
                    "–"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && <footer className="disclaimer">{data.disclaimer}</footer>}
    </main>
  );
}
