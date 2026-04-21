// FastAPI 백엔드 호출 래퍼. JWT HttpOnly 쿠키 동행을 위해 credentials: 'include'.
//
// 브라우저에서 호출. NEXT_PUBLIC_API_BASE_URL 로 베이스 URL 주입.

import type {
  ClassifyRequestPayload,
  HSDetail,
  JobCreateResponse,
  JobStatusResponse,
  JobSummary,
  LoginPayload,
  UserMe,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers || {}),
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });

  const text = await res.text();
  const data = text ? safeParseJson(text) : null;

  if (!res.ok) {
    const detail =
      (data && typeof data === "object" && "detail" in (data as object)
        ? (data as { detail: unknown }).detail
        : data) ?? text;
    const msg =
      typeof detail === "string"
        ? detail
        : `요청 실패 (${res.status})`;
    throw new ApiError(res.status, msg, detail);
  }
  return data as T;
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// ---- Auth ----

export async function login(payload: LoginPayload): Promise<UserMe> {
  return request<UserMe>("/auth/login", {
    method: "POST",
    json: payload,
  });
}

export async function logout(): Promise<void> {
  await request<void>("/auth/logout", { method: "POST" });
}

export async function fetchMe(): Promise<UserMe> {
  return request<UserMe>("/auth/me");
}

// ---- Classify ----

export async function createClassifyJob(
  payload: ClassifyRequestPayload,
): Promise<JobCreateResponse> {
  return request<JobCreateResponse>("/classify", {
    method: "POST",
    json: payload,
  });
}

export async function getClassifyJob(
  jobId: string,
): Promise<JobStatusResponse> {
  return request<JobStatusResponse>(`/classify/${jobId}`);
}

export async function listClassifyJobs(
  opts: { limit?: number; offset?: number } = {},
): Promise<JobSummary[]> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  const q = params.toString();
  return request<JobSummary[]>(`/classify${q ? `?${q}` : ""}`);
}

// ---- HS ----

export async function getHsDetail(hsCode: string): Promise<HSDetail> {
  return request<HSDetail>(`/hs/${encodeURIComponent(hsCode)}`);
}

export async function reviewClassifyJob(
  jobId: string,
  opts: { accepted_hs_code?: string; rejected?: boolean },
): Promise<JobStatusResponse> {
  return request<JobStatusResponse>(`/classify/${jobId}/review`, {
    method: "POST",
    json: opts,
  });
}

// ---- Report ----

export function reportHtmlUrl(jobId: string): string {
  return `${API_BASE}/classify/${jobId}/report.html`;
}

export async function downloadReportPdf(jobId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/classify/${jobId}/report.pdf`, {
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      // keep text
    }
    const msg =
      detail && typeof detail === "object" && "detail" in (detail as object)
        ? String((detail as { detail: unknown }).detail)
        : `PDF 다운로드 실패 (${res.status})`;
    throw new ApiError(res.status, msg, detail);
  }
  return res.blob();
}
