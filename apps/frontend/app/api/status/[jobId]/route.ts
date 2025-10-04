import { NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export async function GET(_: Request, { params }: { params: { jobId: string } }) {
  if (!API_BASE) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }
  const res = await fetch(`${API_BASE}/status/${params.jobId}`, {
    cache: "no-store",
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
