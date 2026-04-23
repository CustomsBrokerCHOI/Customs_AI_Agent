"use client";

import { useState } from "react";
import { ApiError, downloadReportPdf, reportHtmlUrl } from "@/lib/api";

export function ReportActions({ jobId }: { jobId: string }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onPdfClick() {
    setPending(true);
    setError(null);
    try {
      const blob = await downloadReportPdf(jobId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `classify-${jobId.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 501) {
          setError("PDF 생성을 지원하지 않습니다. HTML 보기를 이용해 주세요.");
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : "PDF 다운로드 실패");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="rounded-lg border bg-white p-4 shadow-sm">
      <h2 className="mb-2 text-sm font-semibold text-neutral-800">분류의견서</h2>
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={reportHtmlUrl(jobId)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center rounded border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-800 hover:bg-neutral-50"
        >
          HTML 보기 ↗
        </a>
        <button
          type="button"
          onClick={onPdfClick}
          disabled={pending}
          className="inline-flex items-center rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {pending ? "생성 중..." : "PDF 다운로드"}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
    </section>
  );
}
