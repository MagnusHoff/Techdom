import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../../../_lib/api-base";

interface RouteContext {
  params: {
    userId: string;
  };
}

export async function PATCH(request: Request, context: RouteContext) {
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

  const userId = context.params?.userId;
  if (!userId) {
    return NextResponse.json({ error: "Mangler bruker-ID" }, { status: 400 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/auth/users/${userId}/role`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: await request.text(),
      cache: "no-store",
    });
  } catch (error) {
    console.error(`auth/users/${userId}/role proxy failed`, error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for oppdatering av rolle" },
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
      payload ?? { error: payloadText || "Kunne ikke oppdatere bruker" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(payload ?? {});
}
