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

export interface StoredAnalysis {
  id: string;
  title: string;
  address: string;
  image: string | null;
  savedAt: string | null;
  totalScore: number | null;
  riskLevel: string | null;
  price: number | null;
  finnkode: string | null;
  summary: string | null;
  sourceUrl: string | null;
}

export interface StoredAnalysesResponse {
  items: StoredAnalysis[];
}

export interface ProspectJobResult {
  analysis?: AnalysisResponse;
  listing?: Record<string, unknown> | null;
  ai_extract?: ProspectusExtract | null;
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

export interface AuthUser {
  id: number;
  email: string;
  username?: string | null;
  role: "user" | "plus" | "admin";
  is_active: boolean;
  is_email_verified: boolean;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface AuthErrorResponse {
  detail?: string;
  error?: string;
}

export interface UserListResponse {
  total: number;
  items: AuthUser[];
}

export interface PasswordResetRequestPayload {
  email: string;
}

export interface PasswordResetConfirmPayload {
  token: string;
  password: string;
}

export interface UpdateUsernamePayload {
  username: string;
}

export interface ChangePasswordPayload {
  currentPassword: string;
  newPassword: string;
}

export interface EmailVerificationConfirmPayload {
  token: string;
}
