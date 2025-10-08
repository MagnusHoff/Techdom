import { NextResponse } from "next/server";

import { resolveApiBase } from "../_lib/api-base";

export async function POST(request: Request) {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }

  const payload = await request.json();
  let response: Response;
  try {
    response = await fetch(`${apiBase}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    console.error("feedback proxy failed", error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for tilbakemelding" },
      { status: 502 },
    );
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  }

  return NextResponse.json({}, { status: response.status });
}
