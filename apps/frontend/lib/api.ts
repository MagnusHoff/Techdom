import type {
  AnalyzeJobResponse,
  AnalysisPayload,
  AnalysisResponse,
  AuthResponse,
  AuthUser,
  AuthErrorResponse,
  JobStatus,
  StatsResponse,
  UserListResponse,
  PasswordResetConfirmPayload,
  PasswordResetRequestPayload,
  ChangePasswordPayload,
  UpdateUsernamePayload,
  EmailVerificationConfirmPayload,
  EmailVerificationResendPayload,
  StoredAnalysis,
  StoredAnalysesResponse,
  ProspectusExtract,
  AdminChangeUserPasswordPayload,
  AdminUpdateUserPayload,
  UserStatusResponse,
} from "./types";

function withApiPrefix(path: string): string {
  if (!path.startsWith("/")) {
    return `/api/${path}`;
  }
  return path === "/api" || path.startsWith("/api/") ? path : `/api${path}`;
}

function apiFetch(input: RequestInfo | URL, init?: RequestInit) {
  if (typeof input !== "string") {
    return fetch(input, init);
  }

  if (input.startsWith("http")) {
    return fetch(input, init);
  }

  const base = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";

  if (typeof window !== "undefined") {
    return fetch(withApiPrefix(input), init);
  }

  if (base) {
    return fetch(`${base}${input}`, init);
  }

  return fetch(withApiPrefix(input), init);
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const raw = await res.text();
    let parsedMessage: string | null = null;
    try {
      const data = JSON.parse(raw) as AuthErrorResponse;
      parsedMessage = data.detail || data.error || null;
    } catch {
      /* ignore parse errors */
    }
    const message = parsedMessage || raw || `API error ${res.status}`;
    throw new Error(message);
  }
  return (await res.json()) as T;
}

function toNullableNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }
  return null;
}

function normaliseString(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  return null;
}

function mapSavedAnalysis(raw: Record<string, unknown>): StoredAnalysis {
  return {
    id: typeof raw.id === "string" ? raw.id : String(raw.id ?? ""),
    title: typeof raw.title === "string" ? raw.title : normaliseString(raw.address) ?? "",
    address: typeof raw.address === "string" ? raw.address : "",
    image: normaliseString(raw.image_url),
    savedAt: normaliseString(raw.saved_at),
    totalScore: toNullableNumber(raw.total_score),
    riskLevel: normaliseString(raw.risk_level),
    price: toNullableNumber(raw.price),
    finnkode: normaliseString(raw.finnkode),
    summary: normaliseString(raw.summary),
    sourceUrl: normaliseString(raw.source_url),
    analysisKey: normaliseString(raw.analysis_key),
  };
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  username: string;
  password: string;
}

export interface UserSearchParams {
  search?: string;
  limit?: number;
  offset?: number;
}

export interface UpdateUserRolePayload {
  role: AuthUser["role"];
}

export interface UpdateUserAvatarPayload {
  avatarEmoji: string | null;
  avatarColor: string | null;
}

export async function requestPasswordReset(
  payload: PasswordResetRequestPayload,
): Promise<void> {
  const res = await apiFetch("/auth/password-reset/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const message = await res.text();
    throw new Error(message || "Kunne ikke starte tilbakestilling av passord");
  }
  if (res.status === 204 || res.status === 202) {
    return;
  }
  await handleResponse<unknown>(res);
}

export async function confirmPasswordReset(
  payload: PasswordResetConfirmPayload,
): Promise<void> {
  const res = await apiFetch("/auth/password-reset/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 204) {
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Kunne ikke tilbakestille passordet");
  }

  await handleResponse<unknown>(res);
}

export async function verifyEmail(
  payload: EmailVerificationConfirmPayload,
): Promise<void> {
  const res = await apiFetch("/auth/verify-email/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 204) {
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Kunne ikke verifisere e-postadressen");
  }

  await handleResponse<unknown>(res);
}

export async function resendVerificationEmail(
  payload: EmailVerificationResendPayload,
): Promise<void> {
  const res = await apiFetch("/auth/verify-email/resend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 202) {
    return;
  }

  if (res.ok) {
    await handleResponse<unknown>(res);
    return;
  }

  const retryAfterHeader = res.headers.get("Retry-After");
  const raw = await res.text();
  let message = raw;
  try {
    const parsed = JSON.parse(raw) as AuthErrorResponse;
    message = parsed.detail || parsed.error || raw;
  } catch {
    /* ignore parse errors */
  }

  const error = new Error(message || "Kunne ikke sende verifiseringsmail på nytt.");
  (error as Error & { status?: number }).status = res.status;
  if (retryAfterHeader) {
    const retryAfter = Number.parseInt(retryAfterHeader, 10);
    if (!Number.isNaN(retryAfter)) {
      (error as Error & { retryAfter?: number }).retryAfter = retryAfter;
    }
  }
  throw error;
}

export async function updateUsername(payload: UpdateUsernamePayload): Promise<AuthUser> {
  const res = await apiFetch("/auth/me/username", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}

export async function updateUserAvatar(payload: UpdateUserAvatarPayload): Promise<AuthUser> {
  const res = await apiFetch("/auth/me/avatar", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      avatar_emoji: payload.avatarEmoji,
      avatar_color: payload.avatarColor,
    }),
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}

export async function changePassword(payload: ChangePasswordPayload): Promise<void> {
  const res = await apiFetch("/auth/me/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: payload.currentPassword,
      new_password: payload.newPassword,
    }),
  });

  if (res.status === 204) {
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Kunne ikke oppdatere passordet");
  }

  await handleResponse<unknown>(res);
}

export async function incrementUserAnalyses(increment = 1): Promise<AuthUser> {
  const safeIncrement = Number.isFinite(increment) ? Math.max(1, Math.floor(increment)) : 1;
  const res = await apiFetch("/auth/me/analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ increment: safeIncrement }),
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}
export async function runAnalysis(payload: AnalysisPayload): Promise<AnalysisResponse> {
  const res = await apiFetch("/analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<AnalysisResponse>(res);
}

export interface ManualProspectusPayload {
  text: string;
}

export async function analyzeProspectusText(payload: ManualProspectusPayload): Promise<ProspectusExtract> {
  const res = await apiFetch("/prospectus/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<ProspectusExtract>(res);
}

export async function analyzeProspectusPdf(file: File): Promise<ProspectusExtract> {
  const arrayBuffer = await file.arrayBuffer();
  const bytes = new Uint8Array(arrayBuffer);
  let binary = "";
  const chunkSize = 8192;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  let base64: string;
  if (typeof window === "undefined") {
    base64 = Buffer.from(bytes).toString("base64");
  } else {
    base64 = btoa(binary);
  }

  const res = await apiFetch("/prospectus/manual/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, mime: file.type, data: base64 }),
  });
  return handleResponse<ProspectusExtract>(res);
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await apiFetch(`/status/${jobId}`, {
    cache: "no-store",
  });
  return handleResponse<JobStatus>(res);
}

export async function fetchStats(): Promise<StatsResponse> {
  const res = await apiFetch("/stats", {
    cache: "no-store",
  });
  return handleResponse<StatsResponse>(res);
}

export async function fetchUserStatus(): Promise<UserStatusResponse> {
  const res = await apiFetch("/auth/me/status", {
    cache: "no-store",
  });
  return handleResponse<UserStatusResponse>(res);
}

export async function startAnalysisJob(finnkode: string): Promise<AnalyzeJobResponse> {
  const res = await apiFetch("/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finnkode }),
  });
  return handleResponse<AnalyzeJobResponse>(res);
}

export async function loginUser(payload: LoginPayload): Promise<AuthResponse> {
  const res = await apiFetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  return handleResponse<AuthResponse>(res);
}

export async function registerUser(payload: RegisterPayload): Promise<AuthUser> {
  const res = await apiFetch("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<AuthUser>(res);
}

export async function fetchCurrentUser(): Promise<AuthUser> {
  const res = await apiFetch("/auth/me", {
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}

export async function logoutUser(): Promise<void> {
  const res = await apiFetch("/auth/logout", {
    method: "POST",
  });
  if (!res.ok) {
    const message = await res.text();
    throw new Error(message || "Kunne ikke logge ut");
  }
}

export async function fetchUsers(params: UserSearchParams = {}): Promise<UserListResponse> {
  const query = new URLSearchParams();
  if (params.search) {
    query.set("search", params.search.trim());
  }
  if (typeof params.limit === "number") {
    query.set("limit", String(params.limit));
  }
  if (typeof params.offset === "number") {
    query.set("offset", String(params.offset));
  }
  const queryString = query.toString();
  const resource = queryString ? `/auth/users?${queryString}` : "/auth/users";
  const res = await apiFetch(resource, {
    cache: "no-store",
  });
  return handleResponse<UserListResponse>(res);
}

export interface FetchSavedAnalysesParams {
  analysisKey?: string | null;
}

export async function fetchSavedAnalyses(
  params: FetchSavedAnalysesParams = {},
): Promise<StoredAnalysesResponse> {
  const query = new URLSearchParams();
  if (params.analysisKey) {
    query.set("analysisKey", params.analysisKey);
  }
  const queryString = query.toString();
  const resource = queryString ? `/analyses?${queryString}` : "/analyses";
  const res = await apiFetch(resource, {
    cache: "no-store",
  });
  const data = await handleResponse<{ items?: Array<Record<string, unknown>> }>(res);
  const items = Array.isArray(data.items)
    ? data.items.map((item) => mapSavedAnalysis(item as Record<string, unknown>))
    : [];
  return { items };
}

export interface SaveAnalysisPayload {
  analysisKey: string;
  title?: string | null;
  address?: string | null;
  imageUrl?: string | null;
  totalScore?: number | null;
  riskLevel?: string | null;
  price?: number | null;
  finnkode?: string | null;
  summary?: string | null;
  sourceUrl?: string | null;
}

export async function saveAnalysis(payload: SaveAnalysisPayload): Promise<StoredAnalysis> {
  const body = {
    analysis_key: payload.analysisKey,
    title: payload.title ?? null,
    address: payload.address ?? null,
    image_url: payload.imageUrl ?? null,
    total_score: payload.totalScore ?? null,
    risk_level: payload.riskLevel ?? null,
    price: payload.price ?? null,
    finnkode: payload.finnkode ?? null,
    summary: payload.summary ?? null,
    source_url: payload.sourceUrl ?? null,
  };
  const res = await apiFetch("/analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await handleResponse<Record<string, unknown>>(res);
  return mapSavedAnalysis(data);
}

export async function deleteSavedAnalysis(analysisId: string): Promise<void> {
  const res = await apiFetch(`/analyses/${analysisId}`, {
    method: "DELETE",
    cache: "no-store",
  });
  if (res.status === 204) {
    return;
  }
  if (!res.ok) {
    const message = await res.text();
    throw new Error(message || "Kunne ikke fjerne analysen.");
  }
  await handleResponse<unknown>(res);
}

export async function changeUserRole(
  userId: number,
  payload: UpdateUserRolePayload,
): Promise<AuthUser> {
  const res = await apiFetch(`/auth/users/${userId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}

export async function updateUserProfile(
  userId: number,
  payload: AdminUpdateUserPayload,
): Promise<AuthUser> {
  const res = await apiFetch(`/auth/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  return handleResponse<AuthUser>(res);
}

export async function adminChangeUserPassword(
  userId: number,
  payload: AdminChangeUserPasswordPayload,
): Promise<void> {
  const res = await apiFetch(`/auth/users/${userId}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: payload.newPassword }),
    cache: "no-store",
  });

  if (res.status === 204) {
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Kunne ikke oppdatere passordet");
  }

  await handleResponse<unknown>(res);
}

export async function deleteUser(userId: number): Promise<void> {
  const res = await apiFetch(`/auth/users/${userId}`, {
    method: "DELETE",
    cache: "no-store",
  });

  if (res.status === 204) {
    return;
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Kunne ikke slette brukeren");
  }

  await handleResponse<unknown>(res);
}
