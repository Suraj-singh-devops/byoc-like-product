import type { ApiErrorBody } from "./types";

export const UNAUTHORIZED_EVENT = "byoc:unauthorized";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly reason?: string;
  readonly suggestedAction?: string;
  readonly fields: Record<string, string>;
  readonly requestId?: string;
  readonly details?: ApiErrorBody["details"];

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.reason = body.reason;
    this.suggestedAction = body.suggested_action;
    this.fields = body.details?.fields ?? {};
    this.details = body.details;
    this.requestId = body.request_id;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** Calls the control-plane API (proxied under /api/v1). The session is an httpOnly cookie. */
export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, {
      code: "NETWORK_ERROR",
      message: "Could not reach the control plane.",
      suggested_action: "Check that the API is running and try again.",
    });
  }
  if (response.status === 401 && !path.startsWith("/auth/login")) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data = text ? safeJson(text) : undefined;
  if (!response.ok) {
    const error = (data as { error?: ApiErrorBody } | undefined)?.error;
    throw new ApiError(
      response.status,
      error ?? { code: `HTTP_${response.status}`, message: `Request failed with status ${response.status}.` },
    );
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
