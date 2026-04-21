"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, getClassifyJob } from "@/lib/api";
import { CandidateCard } from "@/components/CandidateCard";
import { ReportActions } from "@/components/ReportActions";
import { StageTimeline } from "@/components/StageTimeline";
import type { JobStatusResponse } from "@/lib/types";

const POLL_INTERVAL_MS = 2500;
const TERMINAL_STATES = new Set(["complete", "failed", "cancelled"]);

export default function ClassifyDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number>(Date.now());

  useEffect(() => {
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const res = await getClassifyJob(params.id);
        if (cancelled) return;
        setJob(res);
        setError(null);
        if (!TERMINAL_STATES.has(res.status)) {
          timeoutId = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(err.status === 401 ? "로그인이 필요합니다." : err.message);
        } else {
          setError(err instanceof Error ? err.message : "조회 실패");
        }
      }
    }

    tick();
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000)),
      500,
    );

    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
      clearInterval(timer);
    };
  }, [params.id]);

  if (error) {
    return (
      <section className="space-y-4">
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-red-700">
          {error}
        </div>
        <Link href="/classify/new" className="text-sm text-blue-700 hover:underline">
          ← 새 분류 요청
        </Link>
      </section>
    );
  }

  if (!job) {
    return <LoadingSpinner label={`job ${params.id.slice(0, 8)}… 조회 중`} />;
  }

  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{job.product_name}</h1>
          <p className="mt-1 text-sm text-neutral-600">{job.description}</p>
        </div>
        <StatusBadge status={job.status} elapsed={elapsed} />
      </div>

      {!TERMINAL_STATES.has(job.status) ? (
        <LoadingSpinner
          label={
            job.status === "pending"
              ? "대기 중… 백그라운드 워커가 잡을 가져가는 중입니다."
              : "분류 엔진 실행 중… Input Gate → 부 결정 → 검색 → 검증 (보통 20–60초)"
          }
        />
      ) : null}

      {job.status === "failed" ? (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <strong className="font-semibold">분류 실패.</strong>{" "}
          {job.error_message ?? "원인 미상."}
        </div>
      ) : null}

      {job.result?.notice ? (
        <div className="whitespace-pre-wrap rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {job.result.notice}
        </div>
      ) : null}

      {job.result?.meta?.follow_up_questions &&
      job.result.meta.follow_up_questions.length > 0 ? (
        <FollowUpBlock
          questions={job.result.meta.follow_up_questions}
          jobId={job.id}
        />
      ) : null}

      {job.result?.meta ? <StageTimeline meta={job.result.meta} /> : null}

      {job.result?.candidates && job.result.candidates.length > 0 ? (
        <section>
          <h2 className="mb-3 text-lg font-semibold">후보 HS CODE</h2>
          <ul className="grid gap-3">
            {job.result.candidates.map((c) => (
              <li key={`${c.rank}-${c.heading}`}>
                <CandidateCard candidate={c} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {job.status === "complete" ? <ReportActions jobId={job.id} /> : null}

      <div className="flex items-center justify-between border-t pt-4 text-sm">
        <Link href="/classify/new" className="text-blue-700 hover:underline">
          ← 새 분류 요청
        </Link>
        <span className="text-xs text-neutral-500">
          Job ID: <code className="font-mono">{job.id}</code>
        </span>
      </div>
    </section>
  );
}

function StatusBadge({
  status,
  elapsed,
}: {
  status: string;
  elapsed: number;
}) {
  const map: Record<string, string> = {
    pending: "bg-neutral-100 text-neutral-700",
    processing: "bg-blue-100 text-blue-800",
    complete: "bg-emerald-100 text-emerald-800",
    failed: "bg-red-100 text-red-800",
    cancelled: "bg-neutral-200 text-neutral-700",
  };
  const cls = map[status] ?? map.pending;
  const label: Record<string, string> = {
    pending: "대기",
    processing: "처리 중",
    complete: "완료",
    failed: "실패",
    cancelled: "취소됨",
  };
  return (
    <div className={`rounded-full px-3 py-1 text-xs font-medium ${cls}`}>
      {label[status] ?? status}
      {!TERMINAL_STATES.has(status) ? ` · ${elapsed}초` : null}
    </div>
  );
}

function LoadingSpinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-white px-4 py-6 shadow-sm">
      <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-800" />
      <span className="text-sm text-neutral-700">{label}</span>
    </div>
  );
}

function FollowUpBlock({
  questions,
  jobId,
}: {
  questions: string[];
  jobId: string;
}) {
  return (
    <section className="rounded-lg border border-amber-300 bg-amber-50 p-4">
      <h2 className="mb-2 text-sm font-semibold text-amber-900">
        분류 진행을 위한 추가 정보가 필요합니다
      </h2>
      <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900">
        {questions.map((q, i) => (
          <li key={i}>{q}</li>
        ))}
      </ul>
      <div className="mt-3 text-xs text-amber-800">
        위 정보를 포함하여 새 분류를 재요청하거나, 이 잡(
        <code className="font-mono">{jobId.slice(0, 8)}</code>) 은 보류 상태로
        남겨두세요.
      </div>
    </section>
  );
}
