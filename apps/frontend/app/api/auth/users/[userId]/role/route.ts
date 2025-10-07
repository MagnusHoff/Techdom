import { cookies } from "next/headers";
import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

interface RouteContext {
  params: {
    userId: string;
  };
}

export async function PATCH(request: Request, context: RouteContext) {
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

  const userId = context.params?.userId;
  if (!userId) {
    return NextResponse.json({ error: "Mangler bruker-ID" }, { status: 400 });
  }

  const upstream = await fetch(`${API_BASE}/auth/users/${userId}/role`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: await request.text(),
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
      payload ?? { error: payloadText || "Kunne ikke oppdatere bruker" },
      { status: upstream.status },
    );
  }

  return NextResponse.json(payload ?? {});
}
