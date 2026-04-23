"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, listClassifyJobs } from "@/lib/api";
import { formatHSCode } from "@/lib/hs";
import type { JobStatus, JobSummary } from "@/lib/types";

const PAGE_SIZE = 20;

const STATUS_LABEL: Record<JobStatus, string> = {
  pending: "대기",
  processing: "처리 중",
  complete: "완료",
  failed: "실패",
  cancelled: "취소",
};

const STATUS_CLS: Record<JobStatus, string> = {
  pending: "bg-neutral-100 text-neutral-700",
  processing: "bg-blue-100 text-blue-800",
  complete: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  cancelled: "bg-neutral-200 text-neutral-700",
};

export default function ClassifyHistoryPage() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listClassifyJobs({ limit: PAGE_SIZE, offset })
      .then((res) => {
        if (!cancelled) setJobs(res);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(err.status === 401 ? "로그인이 필요합니다." : err.message);
        } else {
          setError(err instanceof Error ? err.message : "조회 실패");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [offset]);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">분류 히스토리</h1>
          <p className="text-sm text-neutral-600">
            내 분류 잡의 상태·결과·관세사 확인 여부를 최신순으로 표시합니다.
          </p>
        </div>
        <Link
          href="/classify/new"
          className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white"
        >
          + 새 분류
        </Link>
      </div>

      {error ? (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <div className="overflow-hidden rounded-lg border bg-white shadow-sm">
        <table className="min-w-full divide-y divide-neutral-200">
          <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wider text-neutral-600">
            <tr>
              <th className="px-4 py-2">품명</th>
              <th className="px-4 py-2">상태</th>
              <th className="px-4 py-2">확인</th>
              <th className="px-4 py-2">채택 HS</th>
              <th className="px-4 py-2">생성일</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-100">
            {loading && jobs === null ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-sm text-neutral-500">
                  불러오는 중…
                </td>
              </tr>
            ) : jobs && jobs.length > 0 ? (
              jobs.map((j) => (
                <tr key={j.id} className="hover:bg-neutral-50">
                  <td className="px-4 py-2 text-sm">
                    <Link
                      href={`/classify/${j.id}`}
                      className="text-blue-700 hover:underline"
                    >
                      {j.product_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLS[j.status] ?? ""}`}
                    >
                      {STATUS_LABEL[j.status] ?? j.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-sm text-neutral-700">
                    {j.reviewed ? "✓ 확인" : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-sm">
                    {j.accepted_hs_code ? formatHSCode(j.accepted_hs_code) : "—"}
                  </td>
                  <td className="px-4 py-2 text-sm text-neutral-600">
                    {fmtDate(j.created_at)}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-sm text-neutral-500">
                  분류 요청이 없습니다.{" "}
                  <Link href="/classify/new" className="text-blue-700 hover:underline">
                    새 분류 시작 →
                  </Link>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm">
        <button
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          disabled={offset === 0 || loading}
          className="rounded border px-3 py-1.5 text-neutral-700 hover:bg-neutral-50 disabled:opacity-40"
        >
          ← 이전
        </button>
        <span className="text-xs text-neutral-500">
          {offset + 1} ~ {offset + (jobs?.length ?? 0)}
        </span>
        <button
          onClick={() => setOffset(offset + PAGE_SIZE)}
          disabled={loading || (jobs !== null && jobs.length < PAGE_SIZE)}
          className="rounded border px-3 py-1.5 text-neutral-700 hover:bg-neutral-50 disabled:opacity-40"
        >
          다음 →
        </button>
      </div>
    </section>
  );
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
