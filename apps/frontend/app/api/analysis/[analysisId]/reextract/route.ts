import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../../_lib/api-base";
import { buildUpstreamHeaders, resolveAccessToken } from "../../../_lib/auth-proxy";

async function proxyReextractRequest(
  request: NextRequest,
  analysisId: string,
): Promise<NextResponse> {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const token = resolveAccessToken(request);
  const search = request.nextUrl.search;
  const targetUrl = `${apiBase}/analysis/${encodeURIComponent(analysisId)}/reextract${search}`;

  const headers = buildUpstreamHeaders(request, token);

  let body: string | undefined;
  if (request.method.toUpperCase() !== "GET" && request.method.toUpperCase() !== "HEAD") {
    body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
  } catch (error) {
    console.error("analysis reextract proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for TG-ekstraksjon" },
      { status: 502 },
    );
  }

  if (upstream.status === 204) {
    const emptyResponse = new NextResponse(null, { status: upstream.status });
    const cacheControl = upstream.headers.get("cache-control");
    if (cacheControl) {
      emptyResponse.headers.set("cache-control", cacheControl);
    }
    return emptyResponse;
  }

  const arrayBuffer = await upstream.arrayBuffer();
  const downstream = new NextResponse(arrayBuffer, { status: upstream.status });

  const contentType = upstream.headers.get("content-type");
  if (contentType) {
    downstream.headers.set("content-type", contentType);
  }
  const cacheControl = upstream.headers.get("cache-control");
  if (cacheControl) {
    downstream.headers.set("cache-control", cacheControl);
  }

  return downstream;
}

export async function POST(
  request: NextRequest,
  context: { params: { analysisId: string } },
): Promise<NextResponse> {
  return proxyReextractRequest(request, context.params.analysisId);
}

export async function GET(
  request: NextRequest,
  context: { params: { analysisId: string } },
): Promise<NextResponse> {
  return proxyReextractRequest(request, context.params.analysisId);
}
