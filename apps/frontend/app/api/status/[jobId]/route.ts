import { NextResponse } from "next/server";

import { resolveApiBase } from "../../_lib/api-base";

export async function GET(_: Request, { params }: { params: { jobId: string } }) {
  const apiBase = resolveApiBase();
  if (!apiBase) {
    return NextResponse.json({ error: "NEXT_PUBLIC_API_BASE_URL mangler" }, { status: 500 });
  }
  let res: Response;
  try {
    res = await fetch(`${apiBase}/status/${params.jobId}`, {
      cache: "no-store",
    });
  } catch (error) {
    console.error(`status/${params.jobId} proxy failed`, error);
    return NextResponse.json(
      { error: "Kunne ikke kontakte API-et for status" },
      { status: 502 },
    );
  }

  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!res.ok) {
    return NextResponse.json(
      data ?? { error: text || "Status-forespørsel feilet" },
      { status: res.status },
    );
  }

  return NextResponse.json(data ?? {}, { status: res.status });
}
