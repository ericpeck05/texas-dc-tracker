// Shared pieces for the summary-first pages: status chips, the build-stage bar, plain
// labels for each kind of filing, bar lists, and the "evidence" drawer that holds the raw
// tables for anyone who wants them.
import type { ReactNode } from "react";
import { mw, num } from "./format";

export const STATUS_LABEL: Record<string, string> = {
  rumored: "Announced",
  filed: "Filed",
  approved: "Approved",
  under_construction: "Under construction",
  operating: "Operating",
  cancelled: "Cancelled",
};

export function StatusChip({ status }: { status: string }) {
  return (
    <span className="status-chip" style={{ ["--c" as string]: `var(--st-${status})` }}>
      <span className="status-dot" />
      {STATUS_LABEL[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}

const STAGES = ["rumored", "filed", "approved", "under_construction", "operating"];
const STAGE_HELP: Record<string, string> = {
  rumored: "Made public in a notice or report",
  filed: "Permits or registrations on file",
  approved: "A permit or incentive was granted",
  under_construction: "Construction permits active: dirt is moving",
  operating: "Running",
};

// Where a site is on the road from announcement to operation.
export function StageBar({ status }: { status: string }) {
  if (status === "cancelled")
    return <div className="stage-bar cancelled">Cancelled</div>;
  const at = Math.max(0, STAGES.indexOf(status));
  return (
    <ol className="stage-bar" aria-label={`Stage: ${STATUS_LABEL[status] ?? status}`}>
      {STAGES.map((s, i) => (
        <li
          key={s}
          className={i < at ? "done" : i === at ? "now" : "todo"}
          title={STAGE_HELP[s]}
          style={{ ["--c" as string]: `var(--st-${status})` }}
        >
          <span className="stage-pip" />
          <span className="stage-name">{STATUS_LABEL[s]}</span>
        </li>
      ))}
    </ol>
  );
}

// What each kind of filing means, in plain words.
export const KIND: Record<string, { label: string; means: string }> = {
  air_permit: { label: "Air permit", means: "on-site generators or turbines" },
  stormwater: { label: "Construction permit", means: "stormwater permit before moving dirt" },
  incentive: { label: "Tax exemption", means: "state data center sales-tax registry" },
  approval: { label: "Tax abatement", means: "local property tax deal" },
  grid: { label: "Grid case", means: "Texas PUC docket on power for the site" },
  sec_mention: { label: "SEC filing", means: "named in a public company's filing" },
  notice: { label: "Public notice", means: "announced before any permit" },
};

export function KindTag({ type }: { type: string }) {
  return (
    <span className={`kind-tag kind-${type}`} title={KIND[type]?.means}>
      {KIND[type]?.label ?? type.replace(/_/g, " ")}
    </span>
  );
}

export function powerPhrase(p: {
  generators: number;
  turbines: number;
  mw: number;
  unsized?: number;
}): string | null {
  const parts = [];
  if (p.generators) parts.push(`${num(p.generators)} generator${p.generators === 1 ? "" : "s"}`);
  if (p.turbines) parts.push(`${num(p.turbines)} turbine${p.turbines === 1 ? "" : "s"}`);
  if (!parts.length) return null;
  const size = p.mw ? ` · ${p.unsized ? "at least " : ""}${mw(p.mw)} MW` : "";
  return parts.join(" + ") + size;
}

export function placePhrase(county: string | null, city?: string | null): string {
  if (city && county) return `${city}, ${county} County`;
  if (county) return `${county} County`;
  if (city) return city;
  return "Location unknown";
}

export function Companies({
  items,
  empty = "No parent company traced yet",
}: {
  items: { company: string; ticker: string | null; confidence: string; roles?: string[] }[];
  empty?: string;
}) {
  if (!items.length) return <span className="dim">{empty}</span>;
  return (
    <span className="co-list">
      {items.map((c) => (
        <span key={c.company} className="co" title={c.confidence === "medium" ? "Likely: tied by a name match, not a filing that names the company" : "Named in a filing"}>
          {c.ticker ? (
            <a href={`#/t/${c.ticker}`} className="co-name" onClick={(e) => e.stopPropagation()}>
              {c.company} <span className="sym">{c.ticker}</span>
            </a>
          ) : (
            <span className="co-name">{c.company}</span>
          )}
          {c.roles && c.roles.length > 0 && <span className="dim"> · {c.roles.join(", ")}</span>}
          {c.confidence === "medium" && <span className="likely">likely</span>}
        </span>
      ))}
    </span>
  );
}

// The drawer that keeps raw records one click away instead of on the page.
export function Evidence({
  title = "Show the evidence",
  hint,
  children,
}: {
  title?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <details className="evidence-box">
      <summary>
        <span className="evidence-title">{title}</span>
        {hint && <span className="dim"> · {hint}</span>}
      </summary>
      <div className="evidence-body">{children}</div>
    </details>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

// A ranked list with one bar per row: the plain way to compare a handful of numbers.
export function BarList({
  rows,
  format = num,
  max,
}: {
  rows: { key: string; label: ReactNode; value: number; sub?: ReactNode; muted?: boolean }[];
  format?: (n: number) => string;
  max?: number;
}) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="bar-list">
      {rows.map((r) => (
        <div key={r.key} className={`bar-row${r.muted ? " muted" : ""}`}>
          <div className="bar-label">
            {r.label}
            {r.sub && <div className="bar-sub">{r.sub}</div>}
          </div>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(r.value / top) * 100}%` }} />
          </div>
          <div className="bar-value">{format(r.value)}</div>
        </div>
      ))}
    </div>
  );
}

export function ago(date: string | null | undefined, today = new Date()): string {
  if (!date) return "undated";
  const days = Math.round((today.getTime() - new Date(date + "T00:00:00").getTime()) / 86_400_000);
  if (days < 0) return date;
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  if (days < 365 * 2) return `${Math.round(days / 30.4)} months ago`;
  return `${Math.round(days / 365)} years ago`;
}

export function longDate(date: string): string {
  return new Date(date + "T00:00:00").toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
