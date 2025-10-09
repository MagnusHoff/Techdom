import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../../../_lib/api-base";

export async function POST(request: Request) {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const token = cookies().get("access_token")?.value;
  if (!token) {
    return NextResponse.json({ error: "Ikke autentisert" }, { status: 401 });
  }

  let payload: unknown = null;
  try {
    payload = await request.json();
  } catch {
    payload = null;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/auth/me/subscription/checkout`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: payload ? JSON.stringify(payload) : undefined,
      cache: "no-store",
    });
  } catch (error) {
    console.error("subscription checkout proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for Stripe Checkout" },
      { status: 502 },
    );
  }

  const text = await upstream.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!upstream.ok) {
    return NextResponse.json(
      data ?? { error: text || "Kunne ikke opprette Stripe Checkout" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(data ?? {});
}
