import { NextResponse } from "next/server";

function parseTargetUrl(raw: string | null): URL | null {
  if (!raw) {
    return null;
  }
  try {
    const url = new URL(raw.trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function extractFilenameFromDisposition(headerValue: string | null): string | null {
  if (!headerValue) {
    return null;
  }

  const filenameStar = headerValue.match(/filename\*=(?:utf-8'')?([^;]+)/i);
  if (filenameStar && filenameStar[1]) {
    try {
      return decodeURIComponent(filenameStar[1].replace(/^"(.*)"$/, "$1").trim());
    } catch {
      /* ignore invalid encoding */
    }
  }

  const filenameMatch = headerValue.match(/filename=(?:\"([^\"]+)\"|([^;]+))/i);
  if (filenameMatch) {
    return (filenameMatch[1] ?? filenameMatch[2] ?? "").trim();
  }

  return null;
}

function sanitiseFilename(name: string | null, fallback: string): string {
  const candidate = (name ?? "").trim();
  if (!candidate) {
    return fallback;
  }
  return candidate
    .replace(/[\r\n]/g, "")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, " ");
}

function buildInlineDisposition(filename: string): string {
  return `inline; filename="${filename}"`;
}

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const targetParam = requestUrl.searchParams.get("url");
  const targetUrl = parseTargetUrl(targetParam);
  if (!targetUrl) {
    return NextResponse.json({ error: "Ugyldig eller manglende URL" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: "GET",
      headers: {
        Accept: "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
      },
    });
  } catch (error) {
    console.error("prospectus pdf proxy failed", error);
    return NextResponse.json({ error: "Kunne ikke hente PDF" }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    const status = upstream.status || 502;
    return NextResponse.json(
      { error: "Kunne ikke åpne salgsoppgaven", status },
      { status },
    );
  }

  const upstreamContentType = upstream.headers.get("content-type") ?? "";
  const contentType = upstreamContentType.toLowerCase().includes("pdf")
    ? upstreamContentType
    : "application/pdf";

  const filenameFromHeader = extractFilenameFromDisposition(upstream.headers.get("content-disposition"));
  const fallbackFilename = targetUrl.pathname.split("/").pop() || "salgsoppgave.pdf";
  const filename = sanitiseFilename(filenameFromHeader ?? fallbackFilename, "salgsoppgave.pdf");
  const disposition = buildInlineDisposition(filename);

  const headers = new Headers();
  headers.set("Content-Type", contentType);
  headers.set("Content-Disposition", disposition);
  headers.set("Cache-Control", "no-store");

  const contentLength = upstream.headers.get("content-length");
  if (contentLength) {
    headers.set("Content-Length", contentLength);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}
