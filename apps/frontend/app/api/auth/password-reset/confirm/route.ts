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
    upstream = await fetch(`${apiBase}/auth/password-reset/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch (error) {
    console.error("auth/password-reset/confirm proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for passordbekreftelse" },
      { status: 502 },
    );
  }

  if (upstream.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  const payloadText = await upstream.text();
  let payload: unknown = null;
  try {
    payload = payloadText ? JSON.parse(payloadText) : null;
  } catch {
    payload = null;
  }

  if (!upstream.ok) {
    const bodyPayload =
      payload && typeof payload === "object"
        ? payload
        : { error: payloadText || "Kunne ikke tilbakestille passord" };
    return NextResponse.json(bodyPayload, { status: upstream.status });
  }

  return NextResponse.json(payload ?? {}, { status: upstream.status });
}
