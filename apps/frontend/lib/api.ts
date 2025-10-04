import { AnalyzeJobResponse, AnalysisPayload, AnalysisResponse, JobStatus, StatsResponse } from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `API error ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function runAnalysis(payload: AnalysisPayload): Promise<AnalysisResponse> {
  if (!BASE_URL) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL mangler. Sett den i .env.");
  }
  const res = await fetch(`${BASE_URL}/analysis`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleResponse<AnalysisResponse>(res);
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  const res = await fetch(`/api/status/${jobId}`, {
    cache: "no-store",
  });
  return handleResponse<JobStatus>(res);
}

export async function fetchStats(): Promise<StatsResponse> {
  if (!BASE_URL) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL mangler. Sett den i .env.");
  }
  const res = await fetch(`${BASE_URL}/stats`, {
    cache: "no-store",
  });
  return handleResponse<StatsResponse>(res);
}

export async function startAnalysisJob(finnkode: string): Promise<AnalyzeJobResponse> {
  const res = await fetch(`/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finnkode }),
  });
  return handleResponse<AnalyzeJobResponse>(res);
}
