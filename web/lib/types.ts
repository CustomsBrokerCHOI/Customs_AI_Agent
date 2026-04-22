// API 응답 스키마 미러. api/schemas/classify.py 와 동기화 유지.

export type Verdict = "match" | "mismatch" | "uncertain" | "unverified";

export type JobStatus =
  | "pending"
  | "processing"
  | "complete"
  | "failed"
  | "cancelled";

export type SourceKind =
  | "heading_note"
  | "section_note"
  | "chapter_note"
  | "general_rule"
  | "case";

export interface Citation {
  source_kind: SourceKind | string;
  heading: string | null;
  excerpt: string;
  full_text_url?: string | null;
}

export interface Candidate {
  rank: number;
  hs_code: string | null;
  name_kr: string | null;
  name_en: string | null;
  heading: string; // 4자리
  sub_heading: string; // 2자리 ("" 허용)
  breadcrumb: string[];
  confidence: number; // 0~1
  base_tariff_rate: string | null;
  verified: boolean;
  verdict: Verdict | string;
  citations: Citation[];
}

export interface ClassifyResult {
  candidates: Candidate[];
  notice: string | null;
  // 엔진이 내는 추가 필드는 result 에 함께 담겨있음 (meta 등). 서버 ClassifyResult
  // 스키마에는 없지만 job.result JSON 에는 포함. 타입은 optional 로 보수적으로 선언.
  meta?: EngineMeta;
}

export interface EngineMeta {
  engine: string;
  draft?: boolean;
  stopped_at?: string;
  reason?: string;
  follow_up_questions?: string[];
  stages?: Record<string, unknown>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    calls?: number;
  };
}

export interface JobStatusResponse {
  id: string;
  status: JobStatus;
  product_name: string;
  description: string;
  result: ClassifyResult | null;
  error_message: string | null;
  reviewed: boolean;
  accepted_hs_code: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface JobCreateResponse {
  job_id: string;
  status: JobStatus;
  created_at: string;
}

export interface JobSummary {
  id: string;
  status: JobStatus;
  product_name: string;
  reviewed: boolean;
  accepted_hs_code: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface TariffRow {
  fta_code: string;
  fta_name: string | null;
  tax_rate: number | null;
  per_unit_tax: number | null;
  base_price: number | null;
  apply_start: string | null;
  apply_end: string | null;
  source: string;
}

export interface HSDetail {
  hs_code: string;
  name_kr: string | null;
  name_en: string | null;
  heading: string;
  sub_heading: string;
  tariff_line: string;
  qty_unit: string | null;
  weight_unit: string | null;
  tariff_rates: TariffRow[];
  source: string;
}

export type HSLevel = "chapter" | "heading" | "subheading" | "tariff_line";

export interface SectionInfo {
  roman: string;
  title_kr: string;
  title_en: string;
}

export interface HSChild {
  code: string;
  name_kr: string | null;
  name_en: string | null;
}

export interface HSLookupResponse {
  level: HSLevel;
  code: string;
  chapter_number: number;
  section: SectionInfo | null;
  detail: HSDetail | null;
  children: HSChild[];
}

export interface ClassifyRequestPayload {
  product_name: string;
  description: string;
  image_url?: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  name?: string | null;
}

export interface UserMe {
  id: string;
  email: string;
  name: string | null;
  role: string;
}
