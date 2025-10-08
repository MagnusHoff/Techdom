import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export async function POST(request: Request) {
  if (!API_BASE) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }

  const payload = await request.json();
  const response = await fetch(`${API_BASE}/prospectus/manual/upload`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const clone = response.clone();
  try {
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch {
    const text = await clone.text();
    const message = text || "Kunne ikke analysere salgsoppgaven.";
    return NextResponse.json({ error: message }, { status: response.status });
  }
}
