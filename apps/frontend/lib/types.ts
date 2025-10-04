export interface DecisionStatus {
  score?: number;
  dom?: string;
  setning?: string;
}

export interface ScoreGauge {
  value?: number;
  farge?: string;
}

export interface DecisionUi {
  status?: DecisionStatus;
  scorelinjal?: ScoreGauge;
  tiltak?: string[];
  positivt?: string[];
  risiko?: string[];
  nokkel_tall?: Array<Record<string, unknown>>;
  dom_notat?: string | null;
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
  listing?: Record<string, unknown> | null;
  ai_extract?: Record<string, unknown> | null;
  rent_estimate?: Record<string, unknown> | null;
  interest_estimate?: Record<string, unknown> | null;
  pdf_text_excerpt?: unknown;
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
