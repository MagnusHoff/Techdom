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
    upstream = await fetch(`${apiBase}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      cache: "no-store",
    });
  } catch (error) {
    console.error("auth/login proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for innlogging" },
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
    return NextResponse.json(payload ?? { error: payloadText || "Innlogging feilet" }, {
      status: upstream.status,
    });
  }

  const response = NextResponse.json(payload ?? {});
  const accessToken =
    typeof payload === "object" && payload && "access_token" in payload
      ? String((payload as Record<string, unknown>).access_token ?? "")
      : "";

  if (accessToken) {
    response.cookies.set({
      name: "access_token",
      value: accessToken,
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60,
      path: "/",
    });
  }

  return response;
}
