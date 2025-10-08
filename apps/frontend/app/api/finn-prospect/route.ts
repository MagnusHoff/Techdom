import { NextResponse } from "next/server";

const NEEDLES = [
  "salgsoppgave",
  "komplett salgsoppgave",
  "se salgsoppgave",
  "prospekt",
  "se prospekt",
  "last ned prospekt",
  "last ned salgsoppgave",
  "digital salgsoppgave",
  "digital-salgsoppgave",
  "digital_salgsoppgave",
];

const URL_POSITIVE = ["prospekt", "prospect", "salgsoppgav", "komplett"];
const URL_NEGATIVE = [
  "tilstandsrapport",
  "boligsalgsrapport",
  "egenerkl",
  "energiattest",
  "energimerke",
  "nabolag",
  "nabolagsprofil",
  "budskjema",
  "finansieringsplan",
  "oppsummering",
  "takstrapport",
  "rapport.pdf",
];

const PDF_REGEX = /\.pdf(?:$|\?)/i;
const UUID_REGEX = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const DNB_HOST_SUFFIXES = ["dnbeiendom.no", "dnb-eiendom.no"];
const DNB_ORIGIN = "https://dnbeiendom.no";
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);

type Candidate = {
  url: string;
  score: number;
};

function ensureHttpUrl(raw: string): URL | null {
  try {
    const normalised = raw.trim();
    if (!normalised) {
      return null;
    }
    if (/^https?:\/\//i.test(normalised)) {
      return new URL(normalised);
    }
    return new URL(`https://${normalised}`);
  } catch {
    return null;
  }
}

function resolveUrl(base: URL, href: string): string | null {
  if (!href) {
    return null;
  }
  try {
    let target: URL;
    if (href.startsWith("//")) {
      target = new URL(`${base.protocol}${href}`);
    } else {
      target = new URL(href, base);
    }
    if (target.protocol !== "http:" && target.protocol !== "https:") {
      return null;
    }
    target.hash = "";
    return target.toString();
  } catch {
    return null;
  }
}

function stripHtml(value: string): string {
  return value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

function includesNeedle(value: string): boolean {
  const lower = value.toLowerCase();
  return NEEDLES.some((needle) => lower.includes(needle));
}

function scoreCandidate(url: string, context: string): number {
  const lowerUrl = url.toLowerCase();
  if (URL_NEGATIVE.some((needle) => lowerUrl.includes(needle))) {
    return 0;
  }
  let score = 0;
  if (PDF_REGEX.test(lowerUrl)) {
    score += 5;
  }
  for (const good of URL_POSITIVE) {
    if (lowerUrl.includes(good)) {
      score += 3;
    }
  }
  if (!score) {
    return 0;
  }
  if (includesNeedle(context)) {
    score += 2;
  }
  return score;
}

function collectAnchorCandidates(base: URL, html: string, acc: Map<string, Candidate>) {
  const anchorRegex =
    /<a\b[^>]*href\s*=\s*(?:"([^"]+)"|'([^']+)'|([^"'\s>]+))[^>]*>(.*?)<\/a>/gis;
  let match: RegExpExecArray | null;
  while ((match = anchorRegex.exec(html)) !== null) {
    const href = match[1] ?? match[2] ?? match[3] ?? "";
    const resolved = resolveUrl(base, href);
    if (!resolved) {
      continue;
    }
    const inner = stripHtml(match[4] ?? "");
    const bundle = `${inner} ${match[0]}`.toLowerCase();
    const score = scoreCandidate(resolved, bundle);
    if (!score) {
      continue;
    }
    const existing = acc.get(resolved);
    if (!existing || score > existing.score) {
      acc.set(resolved, { url: resolved, score });
    }
  }
}

function collectGenericPdfCandidates(base: URL, html: string, acc: Map<string, Candidate>) {
  const genericRegex = /["']([^"'<>]+\.pdf(?:\?[^"'<>]*)?)["']/gis;
  let match: RegExpExecArray | null;
  while ((match = genericRegex.exec(html)) !== null) {
    const raw = match[1];
    const resolved = resolveUrl(base, raw);
    if (!resolved) {
      continue;
    }
    const score = scoreCandidate(resolved, raw);
    if (!score) {
      continue;
    }
    const existing = acc.get(resolved);
    if (!existing || score > existing.score) {
      acc.set(resolved, { url: resolved, score });
    }
  }
}

function pickProspectUrl(base: URL, html: string): string | null {
  const candidates = new Map<string, Candidate>();
  collectAnchorCandidates(base, html, candidates);
  collectGenericPdfCandidates(base, html, candidates);
  if (!candidates.size) {
    return null;
  }
  const sorted = Array.from(candidates.values()).sort((a, b) => b.score - a.score);
  return sorted[0]?.url ?? null;
}

function isDnbHost(hostname: string): boolean {
  const lower = hostname.toLowerCase();
  return DNB_HOST_SUFFIXES.some((suffix) => lower === suffix || lower.endsWith(`.${suffix}`));
}

function ensureSalgsoppgaveUrl(url: URL): URL {
  const copy = new URL(url.toString());
  if (copy.pathname.endsWith("/salgsoppgave")) {
    return copy;
  }
  const trimmed = copy.pathname.replace(/\/+$/, "");
  copy.pathname = `${trimmed}/salgsoppgave`;
  return copy;
}

function extractNextData(html: string): unknown {
  const match = html.match(
    /<script[^>]*id=["']__NEXT_DATA__["'][^>]*>(.*?)<\/script>/is,
  );
  if (!match) {
    return null;
  }
  try {
    return JSON.parse(match[1] ?? "{}");
  } catch {
    return null;
  }
}

function findUuidIn(value: unknown): string | null {
  if (!value || typeof value !== "object") {
    if (typeof value === "string" && UUID_REGEX.test(value)) {
      const found = value.match(UUID_REGEX);
      return found ? found[0].toLowerCase() : null;
    }
    return null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const hit = findUuidIn(item);
      if (hit) {
        return hit;
      }
    }
    return null;
  }
  for (const entry of Object.values(value as Record<string, unknown>)) {
    const hit = findUuidIn(entry);
    if (hit) {
      return hit;
    }
  }
  return null;
}

function extractDnbUuid(html: string): string | null {
  const nextData = extractNextData(html);
  const fromJson = findUuidIn(nextData);
  if (fromJson) {
    return fromJson;
  }
  const fromHtml = html.match(UUID_REGEX);
  return fromHtml ? fromHtml[0] : null;
}

async function resolveDnbProspect(candidate: URL, listing: URL): Promise<string | null> {
  if (PDF_REGEX.test(candidate.pathname)) {
    return candidate.toString();
  }

  const refererUrl = ensureSalgsoppgaveUrl(candidate);
  const baseHeaders: Record<string, string> = {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
  };

  const pageResponse = await fetch(refererUrl.toString(), {
    headers: {
      ...baseHeaders,
      Referer: listing.toString(),
    },
    redirect: "follow",
  });
  if (!pageResponse.ok) {
    return null;
  }
  const html = await pageResponse.text();
  const uuid = extractDnbUuid(html);
  if (!uuid) {
    return null;
  }

  const directPdfUrl = `${DNB_ORIGIN}/api/v1/properties/${uuid}/documents/${uuid}.pdf`;
  const pdfDownloadUrl = `${DNB_ORIGIN}/api/v1/properties/${uuid}/pdfdownload`;
  const requestHeaders: Record<string, string> = {
    ...baseHeaders,
    Accept: "application/pdf,application/json,*/*",
    Referer: refererUrl.toString(),
    Origin: DNB_ORIGIN,
    "Content-Type": "application/json",
  };
  let resolvedUrl: string | null = null;

  try {
    const pdfResponse = await fetch(pdfDownloadUrl, {
      method: "POST",
      headers: requestHeaders,
      body: "{}",
      redirect: "manual",
    });

    if (REDIRECT_STATUSES.has(pdfResponse.status)) {
      const location = pdfResponse.headers.get("location");
      if (location) {
        const resolved = resolveUrl(refererUrl, location);
        if (resolved) {
          resolvedUrl = resolved;
          return resolvedUrl;
        }
      }
    }

    if (pdfResponse.ok) {
      const contentType = pdfResponse.headers.get("content-type")?.toLowerCase() ?? "";
      if (contentType.includes("application/json")) {
        try {
          const payload = await pdfResponse.json();
          if (payload && typeof payload === "object") {
            for (const key of ["url", "href", "file", "downloadUrl"]) {
              const candidateUrl = (payload as Record<string, unknown>)[key];
              if (typeof candidateUrl === "string" && candidateUrl) {
                const resolved = resolveUrl(refererUrl, candidateUrl);
                if (resolved) {
                  resolvedUrl = resolved;
                  return resolvedUrl;
                }
              }
            }
          }
        } catch {
          /* fallthrough */
        }
      }
      if (contentType.includes("application/pdf")) {
        resolvedUrl = directPdfUrl;
        return resolvedUrl;
      }
    }
  } catch {
    /* ignore fetch failure and fall back */
  }

  if (!resolvedUrl) {
    try {
      const headResponse = await fetch(directPdfUrl, {
        method: "HEAD",
        headers: requestHeaders,
        redirect: "manual",
      });
      if (REDIRECT_STATUSES.has(headResponse.status)) {
        const location = headResponse.headers.get("location");
        if (location) {
          const resolved = resolveUrl(refererUrl, location);
          if (resolved) {
            resolvedUrl = resolved;
          }
        }
      } else if (headResponse.ok || headResponse.status === 405) {
        resolvedUrl = directPdfUrl;
      }
    } catch {
      /* ignore */
    }
  }

  return resolvedUrl ?? candidate.toString();
}

async function resolveProspectUrl(candidateUrl: string, listing: URL): Promise<string | null> {
  let url: URL;
  try {
    url = new URL(candidateUrl);
  } catch {
    return null;
  }

  if (!isDnbHost(url.hostname)) {
    return null;
  }

  try {
    const resolved = await resolveDnbProspect(url, listing);
    return resolved ?? null;
  } catch {
    return null;
  }
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const listing = url.searchParams.get("listing");
  if (!listing) {
    return NextResponse.json({ error: "listing parameter is required" }, { status: 400 });
  }

  const target = ensureHttpUrl(listing);
  if (!target || !target.hostname.endsWith("finn.no")) {
    return NextResponse.json({ error: "listing must be a FINN-annonse" }, { status: 400 });
  }

  let response: Response;
  try {
    response = await fetch(target.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nb-NO,nb;q=0.9,en-US;q=0.8,en;q=0.7",
      },
      cache: "no-store",
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Kunne ikke hente FINN-annonsen", detail: String(error) },
      { status: 502 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { error: `FINN svarte med ${response.status}` },
      { status: response.status },
    );
  }

  const html = await response.text();
  const prospectUrl = pickProspectUrl(target, html);
  if (!prospectUrl) {
    return NextResponse.json({ url: null }, { status: 404 });
  }

  let finalUrl = prospectUrl;
  const resolved = await resolveProspectUrl(prospectUrl, target);
  if (resolved) {
    finalUrl = resolved;
  }

  return NextResponse.json({ url: finalUrl });
}
