import { useEffect, useRef, useState } from "react";
import { fetchJson, num, signalLabel } from "./format";
import type { FeedItem, FeedResponse } from "./types";
import { Companies, KIND, KindTag, StatusChip, ago, longDate, placePhrase, powerPhrase } from "./ui";

function groupByDay(items: FeedItem[]): [string, { it: FeedItem; i: number }[]][] {
  const out = new Map<string, { it: FeedItem; i: number }[]>();
  items.forEach((it, i) => out.set(it.date, [...(out.get(it.date) ?? []), { it, i }]));
  return [...out.entries()];
}

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
  const rows = useRef<(HTMLDivElement | null)[]>([]);

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
      <h1 className="page-title">Latest filings</h1>
      <p className="page-sub">
        Every new permit, registration and notice, newest first, in plain words.
        {data?.latest_filing && ` The most recent is from ${longDate(data.latest_filing)}.`}
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
          <option value="">All kinds of filing</option>
          {TYPES.map((t) => (
            <option key={t} value={t}>
              {KIND[t]?.label ?? signalLabel(t)}
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
        <div className="feed-days">
          {groupByDay(items).map(([day, group]) => (
            <section key={day} className="feed-day">
              <h2 className="feed-date">
                {longDate(day)} <span className="dim">· {ago(day)}</span>
              </h2>
              <div className="feed-items">
                {group.map(({ it, i }) => {
                  const eq = powerPhrase(it.equipment);
                  return (
                    <div
                      key={it.id}
                      ref={(el) => (rows.current[i] = el)}
                      className={`feed-item${i === current ? " current" : ""}`}
                      onClick={() => setCurrent(i)}
                    >
                      <div className="fi-head">
                        <KindTag type={it.type} />
                        {it.site ? (
                          <a href={`#/s/${it.site.id}`} className="fi-site">
                            {it.site.name}
                          </a>
                        ) : (
                          <a href="#/review" className="fi-site dim">
                            Not matched to a site yet
                          </a>
                        )}
                        {it.site && <StatusChip status={it.site.status} />}
                        {it.revised && <span className="accent small">revised</span>}
                      </div>
                      <div className="fi-what">
                        {KIND[it.type]?.means && (
                          <span className="dim">
                            {KIND[it.type].means[0].toUpperCase() + KIND[it.type].means.slice(1)}:{" "}
                          </span>
                        )}
                        {it.url ? (
                          <a href={it.url} target="_blank" rel="noreferrer">
                            {it.title ?? it.permit ?? "document"}
                          </a>
                        ) : (
                          (it.title ?? it.permit)
                        )}
                      </div>
                      <div className="fi-meta">
                        {it.site && <span>{placePhrase(it.site.county)}</span>}
                        {it.companies.length > 0 && (
                          <span>
                            <Companies items={it.companies} />
                          </span>
                        )}
                        {eq && <span>{eq}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
      {data && <footer className="disclaimer">{data.disclaimer}</footer>}
    </main>
  );
}
