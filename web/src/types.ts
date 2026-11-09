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
