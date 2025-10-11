import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

/**
 * Extract the bearer token from either the incoming request cookies or headers.
 * We persist the access token in an HttpOnly cookie, so we have to promote it
 * to an Authorization header before proxying the request to the backend API.
 */
export function resolveAccessToken(request: NextRequest): string | undefined {
  const fromRequest = request.cookies.get("access_token")?.value;
  if (fromRequest) {
    return fromRequest;
  }

  const fromStore = cookies().get("access_token")?.value;
  if (fromStore) {
    return fromStore;
  }

  const header = request.headers.get("cookie");
  if (!header) {
    return undefined;
  }

  const match = header
    .split(";")
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith("access_token="));

  if (!match) {
    return undefined;
  }

  const token = match.slice("access_token=".length);
  return token || undefined;
}

/**
 * Build the upstream headers for the proxied request. We keep the relevant
 * headers from the original request and inject the Authorization header when
 * we have an access token available.
 */
export function buildUpstreamHeaders(
  request: NextRequest,
  accessToken: string | undefined,
): Headers {
  const headers = new Headers(request.headers);

  if (!headers.has("accept")) {
    headers.set("accept", "application/json");
  }

  if (accessToken) {
    headers.set("authorization", `Bearer ${accessToken}`);
  }

  return headers;
}
