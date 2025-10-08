import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { resolveApiBase } from "../../_lib/api-base";

export async function GET(request: Request) {
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

  const url = new URL(request.url);
  const query = url.search ? `?${url.searchParams.toString()}` : "";

  let upstream: Response;
  try {
    upstream = await fetch(`${apiBase}/auth/users${query}`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
    });
  } catch (error) {
    console.error("auth/users proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for brukerliste" },
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
      payload ?? { error: payloadText || "Kunne ikke hente brukere" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(payload ?? {});
}
