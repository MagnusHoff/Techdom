import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export async function POST(request: Request) {
  if (!API_BASE) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }

  const payload = await request.text();
  const response = await fetch(`${API_BASE}/finn/key-numbers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
