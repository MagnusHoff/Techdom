import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../../_lib/api-base";

export async function PATCH(request: Request) {
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

  const body = await request.text();
  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/auth/me/avatar`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body,
      cache: "no-store",
    });
  } catch (error) {
    console.error("auth/me/avatar proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for avatar" },
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
      payload ?? { error: payloadText || "Kunne ikke oppdatere avatar" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(payload ?? {});
}
