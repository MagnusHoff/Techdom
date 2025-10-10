export interface DecisionStatus {
  score?: number;
  dom?: string;
  setning?: string;
}

export interface ScoreGauge {
  value?: number;
  farge?: string;
}

export interface ProspectusDetail {
  label: string;
  short: string;
  hover: string;
  tg?: number | null;
}

export interface ProspectusExtract {
  summary_md?: string;
  tg3?: string[];
  tg2?: string[];
  upgrades?: string[];
  watchouts?: string[];
  questions?: string[];
  links?: ProspectusLinks;
  tg3_details?: ProspectusDetail[];
  tg2_details?: ProspectusDetail[];
  tg_markdown?: string;
  tg_missing_components?: string[];
}

export interface AnalysisTgDetailResponse {
  tg_version: number;
  updated_at?: string | null;
  tg2_details: ProspectusDetail[];
}

export interface ProspectusLinks {
  salgsoppgave_pdf?: string | null;
  confidence?: number | null;
  message?: string | null;
}

export interface SalgsoppgaveFetchResult {
  status: "found" | "not_found" | "uncertain";
  original_pdf_url?: string | null;
  stable_pdf_url?: string | null;
  filesize_bytes?: number | null;
  sha256?: string | null;
  confidence?: number | null;
  log?: string[];
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
  analysisKey?: string | null;
}

export interface StoredAnalysesResponse {
  items: StoredAnalysis[];
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

export interface UserStatusResponse {
  total_user_analyses: number;
  total_last_7_days: number;
  last_run_at: string | null;
}

export interface AnalyzeJobResponse {
  job_id: string;
  status: string;
}

export interface AuthUser {
  id: number;
  email: string;
  username?: string | null;
  avatar_emoji?: string | null;
  avatar_color?: string | null;
  role: "user" | "plus" | "admin";
  is_active: boolean;
  is_email_verified: boolean;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
  subscription_status?: string | null;
  subscription_price_id?: string | null;
  subscription_current_period_end?: string | null;
  subscription_cancel_at_period_end?: boolean;
  created_at: string;
  updated_at: string;
  total_analyses: number;
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

export interface EmailVerificationResendPayload {
  email: string;
}

export interface AdminUpdateUserPayload {
  username: string;
}

export interface AdminChangeUserPasswordPayload {
  newPassword: string;
}
