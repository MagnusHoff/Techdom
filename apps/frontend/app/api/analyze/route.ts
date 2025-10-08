import { NextResponse } from "next/server";

import { resolveApiBase } from "../_lib/api-base";

export async function POST(request: Request) {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }

  const payload = await request.text();
  let response: Response;
  try {
    response = await fetch(`${apiBase}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    });
  } catch (error) {
    console.error("analyze proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for analyse" },
      { status: 502 },
    );
  }

  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    return NextResponse.json(
      data ?? { error: text || "Analyse-forespørsel feilet" },
      { status: response.status },
    );
  }

  return NextResponse.json(data ?? {}, { status: response.status });
}
