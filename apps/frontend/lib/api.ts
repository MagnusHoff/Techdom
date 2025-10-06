import type { AnalyzeJobResponse, AnalysisPayload, AnalysisResponse, JobStatus, StatsResponse } from "./types";

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
    const text = await res.text();
    throw new Error(text || `API error ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function runAnalysis(payload: AnalysisPayload): Promise<AnalysisResponse> {
  const res = await apiFetch("/analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<AnalysisResponse>(res);
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

export async function startAnalysisJob(finnkode: string): Promise<AnalyzeJobResponse> {
  const res = await apiFetch("/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finnkode }),
  });
  return handleResponse<AnalyzeJobResponse>(res);
}
