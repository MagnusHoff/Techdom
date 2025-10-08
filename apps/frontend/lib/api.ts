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

export async function fetchSavedAnalyses(): Promise<StoredAnalysesResponse> {
  const res = await apiFetch("/analyses", {
    cache: "no-store",
  });
  return handleResponse<StoredAnalysesResponse>(res);
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
