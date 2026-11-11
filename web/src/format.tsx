import type { Confidence, DocRef } from "./types";

export const NEW_DAYS = 30;

export function isNew(date: string | null | undefined): boolean {
  if (!date) return false;
  const age =
    (Date.now() - new Date(date + "T00:00:00").getTime()) / 86_400_000;
  return age >= 0 && age <= NEW_DAYS;
}

export function num(n: number): string {
  return n.toLocaleString("en-US");
}

export function mw(value: number, unsized = 0): string {
  if (!value && unsized) return "size n/a";
  const text = value.toLocaleString("en-US", { maximumFractionDigits: 1 });
  return unsized ? `≥${text}` : text;
}

export function Conf({ level }: { level: Confidence }) {
  return <span className={`conf conf-${level}`}>{level}</span>;
}

const TYPE_LABEL: Record<string, string> = {
  air_permit: "air permit",
  incentive: "tax exemption",
  approval: "approval",
  grid: "grid",
  stormwater: "stormwater",
  sec_mention: "SEC",
  notice: "notice",
};

export function signalLabel(type: string): string {
  return TYPE_LABEL[type] ?? type;
}

// A number that links to the document it came from, as the design rules ask.
export function Sourced({
  children,
  docs,
}: {
  children: React.ReactNode;
  docs: DocRef[];
}) {
  const linked = docs.filter((d) => d.url);
  if (!linked.length) return <>{children}</>;
  const title = linked
    .map(
      (d) =>
        `${d.permit ? "Permit " + d.permit : signalLabel(d.type)} ${d.date ?? ""}`,
    )
    .join("\n");
  return (
    <a
      className="num-link"
      href={linked[0].url!}
      target="_blank"
      rel="noreferrer"
      title={title}
    >
      {children}
    </a>
  );
}

export function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

export function fetchJson<T>(url: string): Promise<T> {
  return fetch(url).then(async (r) => {
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail ?? `HTTP ${r.status}`);
    }
    return r.json();
  });
}

const INFERRED: Record<string, string> = {
  document:
    "Inferred: the only maker this permit document names, not stated with these units",
  permit: "Inferred: named in another document for the same permit",
};

// Marks a maker that was not stated in the sentence that counts the units.
export function Inferred({ source }: { source: string | null | undefined }) {
  if (!source || !INFERRED[source]) return null;
  return (
    <span className="inferred" title={INFERRED[source]}>
      inferred
    </span>
  );
}
