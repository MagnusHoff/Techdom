import { NextResponse } from "next/server";

import { resolveApiBase } from "../../_lib/api-base";

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
    upstream = await fetch(`${apiBase}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
  } catch (error) {
    console.error("auth/register proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for registrering" },
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
    return NextResponse.json(
      payload ?? { error: payloadText || "Registrering feilet" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(payload ?? {});
}
