import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../_lib/api-base";

function buildUpstreamHeaders(request: NextRequest, accessToken: string | undefined): HeadersInit {
  const headers: Record<string, string> = {
    Accept: request.headers.get("accept") ?? "application/json",
  };
  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers["Content-Type"] = contentType;
  }
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  return headers;
}

async function proxyAnalysesDetail(
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

  const token = cookies().get("access_token")?.value;
  const search = request.nextUrl.search;
  const targetUrl = `${apiBase}/analyses/${encodeURIComponent(analysisId)}${search}`;

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
    console.error("analyses detail proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for analyser" },
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

export async function GET(
  request: NextRequest,
  context: { params: { analysisId: string } },
): Promise<NextResponse> {
  return proxyAnalysesDetail(request, context.params.analysisId);
}

export async function DELETE(
  request: NextRequest,
  context: { params: { analysisId: string } },
): Promise<NextResponse> {
  return proxyAnalysesDetail(request, context.params.analysisId);
}

export async function PATCH(
  request: NextRequest,
  context: { params: { analysisId: string } },
): Promise<NextResponse> {
  return proxyAnalysesDetail(request, context.params.analysisId);
}
