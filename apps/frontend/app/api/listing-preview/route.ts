import { NextResponse } from "next/server";

function ensureHttpUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) {
    return trimmed;
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  return `https://${trimmed}`;
}

function extractMetaContent(html: string, attribute: "property" | "name", value: string): string | null {
  const metaTagPattern = /<meta[^>]*>/gi;
  const contentPattern = /content\s*=\s*["']([^"']+)["']/i;
  const attrPattern = new RegExp(`${attribute}\\s*=\\s*["']${value}["']`, "i");
  const tags = html.match(metaTagPattern);
  if (!tags) {
    return null;
  }
  for (const tag of tags) {
    if (!attrPattern.test(tag)) {
      continue;
    }
    const contentMatch = tag.match(contentPattern);
    if (contentMatch) {
      return contentMatch[1];
    }
  }
  return null;
}

function findImageUrl(html: string): string | null {
  const candidates = [
    extractMetaContent(html, "property", "og:image"),
    extractMetaContent(html, "name", "twitter:image"),
  ];
  return candidates.find((value) => typeof value === "string" && value.length > 0) ?? null;
}

function findTitle(html: string): string | null {
  const ogTitle = extractMetaContent(html, "property", "og:title");
  if (ogTitle) {
    return ogTitle;
  }
  const titleMatch = html.match(/<title>([^<]+)<\/title>/i);
  return titleMatch ? titleMatch[1].trim() : null;
}

function normaliseAddress(parts: Array<string | undefined | null>): string | null {
  const cleaned = parts
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .filter(Boolean);
  return cleaned.length ? cleaned.join(", ") : null;
}

function extractAddressFromNode(node: unknown): string | null {
  if (!node || typeof node !== "object") {
    return null;
  }
  const record = node as Record<string, unknown>;
  if (record.address) {
    const addr = record.address;
    if (typeof addr === "string") {
      return addr.trim() || null;
    }
    if (addr && typeof addr === "object") {
      const addrRecord = addr as Record<string, unknown>;
      const composed = normaliseAddress([
        typeof addrRecord.streetAddress === "string" ? addrRecord.streetAddress : undefined,
        typeof addrRecord.postalCode === "string" ? addrRecord.postalCode : undefined,
        typeof addrRecord.addressLocality === "string" ? addrRecord.addressLocality : undefined,
        typeof addrRecord.addressRegion === "string" ? addrRecord.addressRegion : undefined,
      ]);
      if (composed) {
        return composed;
      }
    }
  }

  if (Array.isArray(node)) {
    for (const entry of node) {
      const found = extractAddressFromNode(entry);
      if (found) {
        return found;
      }
    }
    return null;
  }

  for (const value of Object.values(record)) {
    if (typeof value === "object" && value) {
      const nested = extractAddressFromNode(value);
      if (nested) {
        return nested;
      }
    }
  }

  return null;
}

function findAddress(html: string): string | null {
  const scriptPattern = /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let match: RegExpExecArray | null;
  while ((match = scriptPattern.exec(html))) {
    const raw = match[1].trim();
    if (!raw) continue;
    try {
      const json = JSON.parse(raw);
      const result = extractAddressFromNode(json);
      if (result) {
        return result;
      }
    } catch {
      // ignore JSON parse errors for malformed blocks
    }
  }

  const fallback = extractMetaContent(html, "property", "og:street-address")
    ?? extractMetaContent(html, "name", "street-address");
  if (fallback && fallback.trim()) {
    return fallback.trim();
  }

  const directMatch = html.match(/"streetAddress"\s*:\s*"([^"]+)"/i);
  if (directMatch && directMatch[1]) {
    const street = directMatch[1].trim();
    const postalMatch = html.match(/"postalCode"\s*:\s*"([^"]+)"/i);
    const localityMatch = html.match(/"addressLocality"\s*:\s*"([^"]+)"/i);
    const parts = [street, postalMatch ? postalMatch[1].trim() : undefined, localityMatch ? localityMatch[1].trim() : undefined]
      .filter((part) => typeof part === "string" && part.length > 0);
    if (parts.length) {
      return parts.join(", ");
    }
    return street;
  }

  const spanMatch = html.match(/<span[^>]*itemprop=["']streetAddress["'][^>]*>([^<]+)<\/span>/i);
  if (spanMatch && spanMatch[1]) {
    const street = spanMatch[1].trim();
    const postalSpan = html.match(/<span[^>]*itemprop=["']postalCode["'][^>]*>([^<]+)<\/span>/i);
    const localitySpan = html.match(/<span[^>]*itemprop=["']addressLocality["'][^>]*>([^<]+)<\/span>/i);
    const parts = [street, postalSpan ? postalSpan[1].trim() : undefined, localitySpan ? localitySpan[1].trim() : undefined]
      .filter((part) => typeof part === "string" && part.length > 0);
    if (parts.length) {
      return parts.join(", ");
    }
    return street;
  }

  return null;
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const rawUrl = searchParams.get("url");
  if (!rawUrl) {
    return NextResponse.json({ image: null, title: null, error: "Missing url" }, { status: 400 });
  }

  const targetUrl = ensureHttpUrl(rawUrl);
  try {
    // Throws if invalid URL (e.g. missing host)
    // eslint-disable-next-line no-new
    new URL(targetUrl);
  } catch {
    return NextResponse.json({ image: null, title: null, error: "Ugyldig URL" }, { status: 400 });
  }

  try {
    const response = await fetch(targetUrl, {
      headers: {
        "user-agent":
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        accept: "text/html,application/xhtml+xml",
      },
      cache: "no-store",
    });

    if (!response.ok) {
      return NextResponse.json({ image: null, title: null, error: `HTTP ${response.status}` }, { status: 502 });
    }

    const html = await response.text();
    const image = findImageUrl(html);
    const title = findTitle(html);
    const address = findAddress(html);

    return NextResponse.json(
      { image: image ?? null, title: title ?? null, address: address ?? null, source: targetUrl },
      {
        status: 200,
        headers: {
          "Cache-Control": "s-maxage=1800, stale-while-revalidate=900",
        },
      },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Kunne ikke hente forhåndsvisning";
    return NextResponse.json({ image: null, title: null, error: message }, { status: 500 });
  }
}
