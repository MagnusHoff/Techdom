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

const IMAGE_KEYWORDS_ALLOW = ["image", "images", "media", "carousel", "gallery", "photo", "photos", "url"];
const IMAGE_KEYWORDS_BLOCK = [
  "logo",
  "ikon",
  "icon",
  "placeholder",
  "map",
  "kart",
  "floorplan",
  "plantegning",
  "fpa",
  "document",
  "doc",
  "pdf",
  "brochure",
  "diagram",
  "overlay",
  "badge",
  "avatar",
  "profile",
  "video",
  "thumb",
  "megler",
  "proff",
  "ansatt",
  "agent",
  "portrait",
  "portrett",
  "team",
  "staff",
  "profil",
  "employee",
  "human",
  "person",
  "people",
  "nav",
];

const IMAGE_IDENTITY_SEGMENT_IGNORE = new Set([
  "original",
  "full",
  "fullsize",
  "master",
  "scaled",
  "scale",
  "web",
  "desktop",
  "mobile",
  "widescreen",
  "optimized",
  "variant",
  "quality",
]);

type ImageCollectorEntry = {
  url: string;
  score: number;
  order: number;
};

type ImageCollectorState = {
  results: Map<string, ImageCollectorEntry>;
  visited: WeakSet<object>;
  order: { value: number };
};

function isLikelyListingImage(url: URL): boolean {
  const hostname = url.hostname.toLowerCase();
  if (!hostname.endsWith("finncdn.no")) {
    return false;
  }

  const path = url.pathname.toLowerCase();
  if (!path.includes("/dynamic/")) {
    return false;
  }

  for (const blocked of IMAGE_KEYWORDS_BLOCK) {
    if (path.includes(blocked)) {
      return false;
    }
  }

  const sizeMatch = path.match(/\/dynamic\/(\d+)(?:x|w)/);
  if (sizeMatch) {
    const size = Number.parseInt(sizeMatch[1], 10);
    if (Number.isFinite(size) && size > 0 && size < 400) {
      return false;
    }
  }

  return true;
}

function createImageCollector(): ImageCollectorState {
  return {
    results: new Map<string, ImageCollectorEntry>(),
    visited: new WeakSet<object>(),
    order: { value: 0 },
  };
}

function extractImageIdentity(url: URL): { key: string; score: number } | null {
  const rawSegments = url.pathname.split("/").filter(Boolean);
  if (rawSegments.length === 0) {
    return null;
  }

  const cleanedSegments: string[] = [];
  let maxWidth = 0;

  for (let i = 0; i < rawSegments.length; i += 1) {
    const segment = decodeURIComponent(rawSegments[i]);
    const lower = segment.toLowerCase();

    if (lower === "dynamic") {
      if (i + 1 < rawSegments.length) {
        const widthCandidate = decodeURIComponent(rawSegments[i + 1]);
        const widthMatch = widthCandidate.match(/(\d{2,4})/);
        if (widthMatch) {
          const width = Number.parseInt(widthMatch[1], 10);
          if (Number.isFinite(width) && width > maxWidth) {
            maxWidth = width;
          }
        }
        if (/^\d+[xw]?$/i.test(widthCandidate) || /^w\d+$/i.test(widthCandidate)) {
          i += 1;
          continue;
        }
      }
      continue;
    }

    if (/^(?:\d{1,4}(?:x|w)?|w\d{1,4}|h\d{1,4}|c\d{1,4}|s\d{1,4}|q\d{1,4}|m\d{1,4}|xs|sm|md|lg)$/i.test(lower)) {
      continue;
    }

    if (IMAGE_IDENTITY_SEGMENT_IGNORE.has(lower)) {
      continue;
    }

    cleanedSegments.push(segment);
  }

  if (cleanedSegments.length === 0) {
    return null;
  }

  const filenameSegment = cleanedSegments[cleanedSegments.length - 1];
  const siblingSegment = cleanedSegments.length > 1 ? cleanedSegments[cleanedSegments.length - 2] : null;

  const filenameLower = filenameSegment.toLowerCase();
  const dotIndex = filenameLower.lastIndexOf(".");
  const baseName = dotIndex > 0 ? filenameLower.slice(0, dotIndex) : filenameLower;
  const trimmedBaseName = baseName.replace(/[-_](?:\d{1,2}|[a-f0-9]{1,4})$/i, "");
  const identityName = trimmedBaseName || baseName || filenameLower;

  const keySegments = [url.hostname.toLowerCase()];
  if (siblingSegment) {
    keySegments.push(siblingSegment.toLowerCase());
  }
  keySegments.push(identityName);

  return { key: keySegments.join("/"), score: maxWidth };
}

function addImageCandidate(state: ImageCollectorState, value: string | null | undefined) {
  if (typeof value !== "string") {
    return;
  }
  const normalised = normaliseImageUrl(value);
  if (!normalised) {
    return;
  }
  let url: URL;
  try {
    url = new URL(normalised);
  } catch {
    return;
  }
  const meta = extractImageIdentity(url);
  if (!meta) {
    return;
  }

  const existing = state.results.get(meta.key);
  if (!existing) {
    state.results.set(meta.key, {
      url: normalised,
      score: meta.score,
      order: state.order.value++,
    });
    return;
  }

  if (meta.score > existing.score) {
    existing.url = normalised;
    existing.score = meta.score;
  }
}

function normaliseImageUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const trimmed = value.trim().replace(/&amp;/g, "&");
  if (!trimmed) {
    return null;
  }
  const withProtocol = trimmed.startsWith("//") ? `https:${trimmed}` : trimmed;
  if (!/^https?:\/\//i.test(withProtocol)) {
    return null;
  }
  try {
    const url = new URL(withProtocol);
    if (!/\.(?:jpe?g|png|webp|avif|gif)(?:[?#].*)?$/i.test(url.pathname + url.search)) {
      return null;
    }
    if (!isLikelyListingImage(url)) {
      return null;
    }
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

function findImageUrl(html: string): string | null {
  const candidates = [
    extractMetaContent(html, "property", "og:image"),
    extractMetaContent(html, "name", "twitter:image"),
  ];
  for (const candidate of candidates) {
    const normalised = normaliseImageUrl(candidate ?? null);
    if (normalised) {
      return normalised;
    }
  }
  return null;
}

function collectImagesFromNode(node: unknown, state: ImageCollectorState) {
  if (node == null) {
    return;
  }
  if (typeof node === "string") {
    addImageCandidate(state, node);
    return;
  }
  if (Array.isArray(node)) {
    for (const entry of node) {
      collectImagesFromNode(entry, state);
    }
    return;
  }
  if (typeof node === "object") {
    const record = node as Record<string, unknown>;
    if (state.visited.has(record)) {
      return;
    }
    state.visited.add(record);

    for (const [key, value] of Object.entries(record)) {
      const lowerKey = key.toLowerCase();
      if (IMAGE_KEYWORDS_BLOCK.some((blocked) => lowerKey.includes(blocked))) {
        continue;
      }
      if (!IMAGE_KEYWORDS_ALLOW.some((keyword) => lowerKey.includes(keyword))) {
        if (typeof value !== "object" || value === null) {
          continue;
        }
      }
      if (typeof value === "string") {
        addImageCandidate(state, value);
      } else {
        collectImagesFromNode(value, state);
      }
    }
  }
}

function collectGalleryImages(html: string): string[] {
  const collector = createImageCollector();

  const scriptPattern = /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let match: RegExpExecArray | null;
  while ((match = scriptPattern.exec(html))) {
    const raw = match[1].trim();
    if (!raw) continue;
    try {
      const json = JSON.parse(raw);
      collectImagesFromNode(json, collector);
    } catch {
      // ignore malformed JSON blocks
    }
  }

  const anyScriptPattern = /<script[^>]*>([\s\S]*?)<\/script>/gi;
  while ((match = anyScriptPattern.exec(html))) {
    const raw = match[1];
    if (!raw || raw.includes("<")) {
      continue;
    }
    const cleaned = raw.trim();
    if (!cleaned.startsWith("{") && !cleaned.startsWith("[") && !cleaned.includes("{")) {
      continue;
    }
    const jsonCandidate = cleaned.replace(/^window\.[^=]+\s*=\s*/, "").replace(/;\s*$/, "");
    if (!jsonCandidate || jsonCandidate.length < 10) {
      continue;
    }
    try {
      const json = JSON.parse(jsonCandidate);
      collectImagesFromNode(json, collector);
    } catch {
      // skip non-JSON script contents
    }
  }

  const urlPattern = /https?:\/\/[^"'\s]+\.(?:jpe?g|png|webp|avif|gif)(?:[?#][^"'\s]*)?/gi;
  while ((match = urlPattern.exec(html))) {
    addImageCandidate(collector, match[0]);
  }

  return Array.from(collector.results.values())
    .sort((a, b) => a.order - b.order)
    .map((entry) => entry.url);
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
    const images = collectGalleryImages(html);
    const primaryOgImage = findImageUrl(html);
    if (primaryOgImage) {
      const existingIndex = images.findIndex((candidate) => candidate === primaryOgImage);
      if (existingIndex === -1) {
        images.unshift(primaryOgImage);
      } else if (existingIndex > 0) {
        images.splice(existingIndex, 1);
        images.unshift(primaryOgImage);
      }
    }
    const image = images.length ? images[0] : primaryOgImage;
    const title = findTitle(html);
    const address = findAddress(html);

    return NextResponse.json(
      {
        image: image ?? null,
        images,
        title: title ?? null,
        address: address ?? null,
        source: targetUrl,
      },
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
