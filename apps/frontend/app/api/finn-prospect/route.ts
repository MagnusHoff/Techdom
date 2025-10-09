import { NextResponse } from "next/server";

import { resolveApiBase } from "../_lib/api-base";

const API_BASE = resolveApiBase();

function buildUpstreamUrl(listing: string, extras: string[]): URL {
  const base = API_BASE ?? "";
  const target = new URL(`${base}/salgsoppgave`);
  target.searchParams.set("finn", listing);
  for (const term of extras) {
    if (term.trim()) {
      target.searchParams.append("extra", term.trim());
    }
  }
  return target;
}

export async function GET(request: Request) {
  if (!API_BASE) {
    return NextResponse.json(
      { error: "NEXT_PUBLIC_API_BASE_URL mangler" },
      { status: 500 },
    );
  }

  const url = new URL(request.url);
  const listing = url.searchParams.get("listing");
  if (!listing) {
    return NextResponse.json(
      { error: "parameter 'listing' mangler" },
      { status: 400 },
    );
  }

  const extras = url.searchParams.getAll("extra");
  const upstreamUrl = buildUpstreamUrl(listing, extras);

  try {
    const upstream = await fetch(upstreamUrl, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await upstream
      .json()
      .catch(() => ({ error: "Ugyldig svar fra API" }));
    return NextResponse.json(payload, { status: upstream.status });
  } catch (error) {
    return NextResponse.json(
      { error: `Kunne ikke hente salgsoppgave: ${error}` },
      { status: 502 },
    );
  }
}
