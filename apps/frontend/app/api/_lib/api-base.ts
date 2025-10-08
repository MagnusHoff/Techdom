const API_BASE_ENV_KEYS = [
  "NEXT_PUBLIC_API_BASE_URL",
  "PUBLIC_API_BASE_URL",
  "API_BASE_URL",
] as const;

export function resolveApiBase(): string | null {
  for (const key of API_BASE_ENV_KEYS) {
    const raw = process.env[key];
    if (typeof raw !== "string") {
      continue;
    }
    const normalized = raw.trim();
    if (!normalized) {
      continue;
    }
    return normalized.replace(/\/$/, "");
  }
  return null;
}
