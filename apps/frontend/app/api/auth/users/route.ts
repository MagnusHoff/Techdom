import { cookies } from "next/headers";
import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export async function GET(request: Request) {
  if (!API_BASE) {
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

  const upstream = await fetch(`${API_BASE}/auth/users${query}`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store",
  });

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
