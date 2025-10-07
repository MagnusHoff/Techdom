import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export async function POST(request: Request) {
  if (!API_BASE) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const body = await request.text();

  const upstream = await fetch(`${API_BASE}/auth/password-reset/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

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
