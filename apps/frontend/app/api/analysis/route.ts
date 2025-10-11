import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../_lib/api-base";
import { buildUpstreamHeaders, resolveAccessToken } from "../_lib/auth-proxy";

async function proxyAnalysisRequest(request: NextRequest): Promise<NextResponse> {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const token = resolveAccessToken(request);
  const search = request.nextUrl.search;
  const targetUrl = `${apiBase}/analysis${search}`;

  const headers = buildUpstreamHeaders(request, token);
  const method = request.method.toUpperCase();

  let body: string | undefined;
  if (method !== "GET" && method !== "HEAD") {
    body = await request.text();
  }

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method,
      headers,
      body,
      cache: "no-store",
    });
  } catch (error) {
    console.error("analysis proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for analyse" },
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

export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAnalysisRequest(request);
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAnalysisRequest(request);
}
