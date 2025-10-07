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

  return NextResponse.json({ url: prospectUrl });
}
