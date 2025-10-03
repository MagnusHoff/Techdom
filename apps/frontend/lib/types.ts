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
  tg2_items: string[];
  tg3_items: string[];
  tg_data_available?: boolean;
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

export interface JobStatus {
  id: string;
  status: string;
  progress?: number;
  message?: string;
  pdf_path?: string | null;
  pdf_url?: string | null;
  created_at?: string;
  updated_at?: string;
}
