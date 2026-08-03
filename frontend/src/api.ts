import type { ApiErrorDetails } from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  details?: ApiErrorDetails;

  constructor(status: number, code: string, message: string, details?: ApiErrorDetails) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function validationMessage(detail: unknown[]): string {
  return detail
    .map((item) => {
      if (!item || typeof item !== "object") return String(item);
      const value = item as { loc?: unknown[]; msg?: string };
      const field = value.loc?.slice(1).join(".");
      return `${field ? `${field}：` : ""}${value.msg ?? "输入不符合要求"}`;
    })
    .join("；");
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return new ApiError(response.status, "http_error", `请求失败（${response.status}）`);
  }
  if (payload && typeof payload === "object") {
    const data = payload as Record<string, unknown>;
    const workflow = data.error;
    if (workflow && typeof workflow === "object") {
      const value = workflow as Record<string, unknown>;
      return new ApiError(
        response.status,
        String(value.code ?? "request_failed"),
        String(value.message ?? "请求失败"),
        value.details as ApiErrorDetails | undefined,
      );
    }
    if (typeof data.detail === "string") {
      return new ApiError(response.status, "http_error", data.detail);
    }
    if (Array.isArray(data.detail)) {
      return new ApiError(response.status, "validation_error", validationMessage(data.detail));
    }
  }
  return new ApiError(response.status, "request_failed", `请求失败（${response.status}）`);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, credentials: "include", headers });
  } catch {
    throw new ApiError(0, "network_error", "无法连接服务器，请检查服务是否正在运行");
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("auth:expired"));
    }
    throw await parseError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "发生未知错误";
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}
