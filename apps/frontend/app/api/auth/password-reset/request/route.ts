import { NextResponse } from "next/server";

import { resolveApiBase } from "../../../_lib/api-base";

export async function POST(request: Request) {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const body = await request.text();
  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/auth/password-reset/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch (error) {
    console.error("auth/password-reset/request proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for passordtilbakestilling" },
      { status: 502 },
    );
  }

  const payloadText = await upstream.text();
  let payload: unknown = null;
  try {
    payload = payloadText ? JSON.parse(payloadText) : null;
  } catch {
    payload = null;
  }

  if (!upstream.ok) {
    const body =
      payload && typeof payload === "object"
        ? payload
        : { error: payloadText || "Kunne ikke starte passordtilbakestilling" };
    return NextResponse.json(body, { status: upstream.status });
  }

  if (payload && typeof payload === "object") {
    return NextResponse.json(payload, { status: upstream.status });
  }

  return NextResponse.json({ status: "accepted" }, { status: upstream.status });
}
