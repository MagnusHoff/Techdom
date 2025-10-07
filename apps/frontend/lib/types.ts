export interface DecisionStatus {
  score?: number;
  dom?: string;
  setning?: string;
}

export interface ScoreGauge {
  value?: number;
  farge?: string;
}

export interface ProspectusExtract {
  summary_md?: string;
  tg3?: string[];
  tg2?: string[];
  upgrades?: string[];
  watchouts?: string[];
  questions?: string[];
  links?: ProspectusLinks;
}

export interface ProspectusLinks {
  salgsoppgave_pdf?: string | null;
  confidence?: number | null;
  message?: string | null;
}

export type KeyFactRaw = {
  label: string;
  value: string;
  order: number;
};

export interface ListingDetailsDTO extends Record<string, unknown> {
  address?: string;
  keyFactsRaw?: KeyFactRaw[];
  key_facts_raw?: KeyFactRaw[];
  keyFacts?: Array<Record<string, unknown>>;
  key_facts?: Array<Record<string, unknown>>;
}

export interface DecisionUi {
  status?: DecisionStatus;
  scorelinjal?: ScoreGauge;
  tiltak?: string[];
  positivt?: string[];
  risiko?: string[];
  nokkel_tall?: Array<Record<string, unknown>>;
  dom_notat?: string | null;
  score_breakdown?: Array<{ id: string; label: string; value: number }>;
  tg_cap_used?: boolean;
}

export interface AnalysisPayload {
  price: string;
  equity: string;
  interest: string;
  term_years: string;
  rent: string;
  hoa: string;
  maint_pct: string;
  vacancy_pct: string;
  other_costs: string;
  tg2_items?: string[];
  tg3_items?: string[];
  tg_data_available?: boolean;
  upgrades?: string[];
  warnings?: string[];
  bath_age_years?: number | null;
  kitchen_age_years?: number | null;
  roof_age_years?: number | null;
}

export interface AnalysisResponse {
  input_params: Record<string, unknown>;
  normalised_params: Record<string, number>;
  metrics: Record<string, number>;
  calculated_metrics: Record<string, unknown> | null;
  decision_result: Record<string, unknown> | null;
  decision_ui: DecisionUi;
  ai_text: string;
}

export interface ProspectJobResult {
  analysis?: AnalysisResponse;
  listing?: ListingDetailsDTO | null;
  ai_extract?: ProspectusExtract | null;
  rent_estimate?: Record<string, unknown> | null;
  interest_estimate?: Record<string, unknown> | null;
  pdf_text_excerpt?: unknown;
  links?: ProspectusLinks | null;
  [key: string]: unknown;
}

export interface JobStatus {
  id: string;
  status: string;
  progress?: number;
  message?: string;
  pdf_path?: string | null;
  pdf_url?: string | null;
  created_at?: string;
  updated_at?: string;
  finnkode?: string;
  result?: ProspectJobResult | null;
  artifacts?: Record<string, unknown> | null;
  payload?: Record<string, unknown> | null;
  error?: string | null;
}

export interface StatsResponse {
  total_analyses: number;
}

export interface AnalyzeJobResponse {
  job_id: string;
  status: string;
}
