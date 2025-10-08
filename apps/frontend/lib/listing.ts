export function normaliseListingUrl(value: string): string {
  if (!value) {
    return "";
  }
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

export function extractFinnkode(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (/^\d{6,}$/.test(trimmed)) {
    return trimmed;
  }

  try {
    const url = new URL(normaliseListingUrl(trimmed));
    const param = url.searchParams.get("finnkode") ?? url.searchParams.get("finnCode");
    if (param) {
      const match = param.match(/\d{6,}/);
      if (match) {
        return match[0];
      }
    }
    const pathMatch = url.pathname.match(/(\d{6,})/);
    if (pathMatch) {
      return pathMatch[1];
    }
  } catch {
    /* ignore invalid URL */
  }

  const fallback = trimmed.match(/(\d{6,})/);
  return fallback ? fallback[1] : null;
}
