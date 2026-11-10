// Shapes returned by the tracker API (src/tracker/views/ticker.py).

export type Confidence = "high" | "medium" | "low";

export interface DocRef {
  permit: string | null;
  date: string | null;
  type: string;
  title: string | null;
  url: string | null;
}

export interface LinkStep {
  confidence: Confidence;
  method: string;
  evidence_url: string | null;
  note: string | null;
}

export interface Tie {
  role: string;
  company: string;
  confidence: Confidence;
  entity: string;
  entity_to_site: LinkStep;
  company_to_entity: LinkStep;
}

export interface Power {
  generators: number;
  turbines: number;
  mw: number;
  unsized: number;
  sources: DocRef[];
}

export interface DemandSite {
  id: number;
  name: string;
  county: string | null;
  status: string;
  signal_types: string[];
  signal_count: number;
  latest_signal: DocRef | null;
  power: Power;
  ties: Tie[];
  confidence: Confidence;
}

export interface SupplyUnit {
  kind: "generators" | "turbines";
  manufacturer: string;
  model: string | null;
  unit_size: number | null;
  unit: string | null;
  count: number;
  mw: number;
  source: DocRef & { page: number | null; snippet: string | null };
}

export interface SupplySite {
  id: number;
  name: string;
  county: string | null;
  status: string;
  units: SupplyUnit[];
  generators: number;
  turbines: number;
  mw: number;
}

export interface TickerView {
  disclaimer: string;
  symbol: string;
  exchange: string | null;
  names: string[];
  roles: string[];
  demand: {
    sites: DemandSite[];
    leads: DemandSite[];
    totals: {
      sites: number;
      high_confidence_sites: number;
      generators: number;
      turbines: number;
      mw: number;
      unsized: number;
    };
  };
  supply: {
    makers: string[];
    sites: SupplySite[];
    totals: { generators: number; turbines: number; mw: number; unsized: number };
  };
  recent_signals: (DocRef & { site: string; site_id: number })[];
}

export interface TickerRow {
  symbol: string;
  exchange: string | null;
  names: string[];
  sites: number;
  high_confidence_sites: number;
  leads: number;
  site_mw: number;
  supplied_units: number;
  supplied_mw: number;
}

// Site list and site page (src/tracker/views/sites.py).

export interface SiteRow {
  id: number;
  name: string;
  county: string | null;
  status: string;
  signal_types: string[];
  sources: number;
  signal_count: number;
  latest_date: string | null;
  generators: number;
  turbines: number;
  mw: number;
  unsized: number;
  companies: { company: string; ticker: string | null; confidence: Confidence }[];
  lat: number | null;
  lon: number | null;
  located: "exact" | "city" | "county" | null;
  place: string | null;
}

export interface SignalRow extends DocRef {
  id: number;
  source: string;
  source_label: string;
  rn: string | null;
  status: string | null;
  equipment_rows: number;
  match: LinkStep | null;
}

export interface EquipmentRow {
  category: string;
  kind: "generators" | "turbines" | null;
  count: number;
  manufacturer: string | null;
  model: string | null;
  fuel: string | null;
  units: string | null;
  unit_size: number | null;
  unit: string | null;
  mw: number | null;
  page: number | null;
  snippet: string | null;
}

export interface ChainCompany extends LinkStep {
  company: string;
  role: string;
  ticker: string | null;
  exchange: string | null;
}

export interface ChainEntity {
  entity: string;
  entity_type: string | null;
  roles: (LinkStep & { role: string })[];
  companies: ChainCompany[];
  affiliates: (LinkStep & { entity: string })[];
  chain_confidence: Confidence | null;
}

export interface SiteView {
  disclaimer: string;
  id: number;
  name: string;
  aliases: string[];
  county: string | null;
  city: string | null;
  status: string;
  status_history: { from: string | null; to: string; at: string | null; note: string | null }[];
  tceq_rns: string[];
  signal_types: string[];
  sources: string[];
  signals: SignalRow[];
  power: Power;
  equipment: (DocRef & { other_documents: number; rows: EquipmentRow[] })[];
  owner_chain: ChainEntity[];
  companies: { company: string; ticker: string | null; confidence: Confidence; roles: string[] }[];
  review: (LinkStep & { site: { id: number; name: string } | null; signal: DocRef | null })[];
  merged_into: { id: number; name: string } | null;
}

export type ReviewKind = "same_site" | "filing" | "parent" | "affiliate";

export type ReviewRecord =
  | (SiteRow & { kind: "site"; city: string | null; tceq_rns: string[]; aliases: string[] })
  | {
      kind: "signal";
      id: number;
      title: string | null;
      url: string | null;
      date: string | null;
      type: string;
      source_label: string;
      site: { id: number; name: string } | null;
    }
  | {
      kind: "entity";
      id: number;
      name: string;
      sites: { id: number; name: string; role: string }[];
      companies: { company: string; confidence: Confidence }[];
    }
  | { kind: "company"; id: number; name: string; ticker: string | null; role: string };

export interface ReviewItem {
  link_id: number;
  kind: ReviewKind;
  method: string;
  note: string | null;
  evidence_url: string | null;
  created_at: string | null;
  left: ReviewRecord;
  right: ReviewRecord;
}

export interface ReviewQueue {
  counts: Record<ReviewKind, number>;
  items: ReviewItem[];
  decided: { confirm: number; reject: number };
}

export interface FeedItem {
  id: number;
  date: string;
  type: string;
  title: string | null;
  url: string | null;
  permit: string | null;
  status: string | null;
  source_label: string;
  site: { id: number; name: string; county: string | null; status: string } | null;
  companies: { company: string; ticker: string | null; confidence: Confidence }[];
  equipment: { generators: number; turbines: number; mw: number };
  revised: boolean;
}

export interface FeedResponse {
  disclaimer: string;
  days: number | null;
  today: string;
  latest_filing: string | null;
  items: FeedItem[];
}

export interface MakerUnitRow {
  maker: string;
  model: string | null;
  kind: "generators" | "turbines";
  count: number;
  unit_size: number | null;
  unit: string | null;
  mw: number;
  unsized: number;
  quarter: string | null;
  first_filed: string | null;
  site: { id: number; name: string; status: string };
  source: DocRef & { page: number | null; snippet: string | null };
}

export interface MakerRow {
  maker: string;
  ticker: string | null;
  exchange: string | null;
  parent: string | null;
  known: boolean;
  generators: number;
  turbines: number;
  mw: number;
  unsized: number;
  sites: number;
  quarters: Record<string, { generators: number; turbines: number; mw: number }>;
  models: { model: string; count: number; mw: number; kind: string }[];
  rows: MakerUnitRow[];
}

export interface Scoreboard {
  disclaimer: string;
  makers: MakerRow[];
  quarters: string[];
  totals: { generators: number; turbines: number; mw: number; unsized: number };
  known_totals: { generators: number; turbines: number; mw: number };
}
