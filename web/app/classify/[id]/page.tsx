"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  createClassifyJob,
  enrichProductInfo,
  getClassifyJob,
} from "@/lib/api";
import { CandidateCard } from "@/components/CandidateCard";
import { ReportActions } from "@/components/ReportActions";
import { StageTimeline } from "@/components/StageTimeline";
import type { EnrichResponse, JobStatusResponse } from "@/lib/types";

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
              : "분류 엔진 실행 중… (보통 20–60초)"
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
          job={job}
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
  job,
}: {
  questions: string[];
  job: JobStatusResponse;
}) {
  const router = useRouter();
  const [pending, setPending] = useState<
    "idle" | "enrich" | "proceed" | "confirm"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [enriched, setEnriched] = useState<EnrichResponse | null>(null);
  const [enrichElapsed, setEnrichElapsed] = useState(0);

  useEffect(() => {
    if (pending !== "enrich") return;
    setEnrichElapsed(0);
    const start = Date.now();
    const id = setInterval(
      () => setEnrichElapsed(Math.floor((Date.now() - start) / 1000)),
      500,
    );
    return () => clearInterval(id);
  }, [pending]);

  // 경과 초에 따라 "검색 단계" 라벨을 약식으로 보여줌 (서버가 단계 신호를 내지 않으므로 근사).
  function enrichLabel(): string {
    if (enrichElapsed < 2) return "Gemini 에 요청 중";
    if (enrichElapsed < 6) return "웹 검색 수행 중";
    if (enrichElapsed < 12) return "결과 정리 중";
    return "응답 대기 중 (과부하 시 자동 재시도)";
  }

  async function onProceedAsIs() {
    setPending("proceed");
    setError(null);
    try {
      const res = await createClassifyJob({
        product_name: job.product_name,
        description: job.description,
        force_classify: true,
      });
      router.push(`/classify/${res.job_id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "재요청 실패"));
      setPending("idle");
    }
  }

  function onEdit() {
    const params = new URLSearchParams({
      name: job.product_name,
      description: job.description,
    });
    router.push(`/classify/new?${params.toString()}`);
  }

  async function onEnrich() {
    setPending("enrich");
    setError(null);
    try {
      const res = await enrichProductInfo({
        product_name: job.product_name,
        image_url: job.image_url ?? undefined,
      });
      setEnriched(res);
    } catch (err) {
      setError(extractErrorMessage(err, "Gemini 보강 실패"));
    } finally {
      setPending("idle");
    }
  }

  async function onConfirmEnriched() {
    if (!enriched) return;
    setPending("confirm");
    setError(null);
    try {
      // Gemini 보강 결과를 채택했다는 건 관세사가 "이 정보로 충분" 판단했다는 의미.
      // Input Gate 가 또 follow-up 을 내면 무한 루프 느낌이 되므로 force_classify
      // 로 bypass 하고 바로 Deep Verify 까지 진행한다.
      const res = await createClassifyJob({
        product_name: job.product_name,
        description: enriched.description,
        image_url: job.image_url ?? undefined,
        force_classify: true,
      });
      router.push(`/classify/${res.job_id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "분류 재요청 실패"));
      setPending("idle");
    }
  }

  const busy = pending !== "idle";

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
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onEdit}
          disabled={busy}
          className="rounded bg-amber-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-60"
        >
          정보 보완해서 재요청
        </button>
        <button
          type="button"
          onClick={onEnrich}
          disabled={busy}
          className="inline-flex items-center gap-2 rounded border border-amber-600 bg-white px-4 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:cursor-wait disabled:opacity-80"
        >
          {pending === "enrich" ? (
            <>
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-amber-500 border-t-transparent" />
              <span>
                {enrichLabel()}
                <AnimatedDots />
              </span>
              <span className="text-xs tabular-nums text-amber-600">
                {enrichElapsed}s
              </span>
            </>
          ) : (
            <>Gemini 로 웹 검색 보강</>
          )}
        </button>
        <button
          type="button"
          onClick={onProceedAsIs}
          disabled={busy}
          className="rounded border border-amber-400 bg-white px-4 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-60"
        >
          {pending === "proceed" ? "요청 중..." : "원본 정보로 진행"}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-700">{error}</p> : null}

      {enriched ? (
        <EnrichedPreview
          data={enriched}
          onConfirm={onConfirmEnriched}
          onCancel={() => setEnriched(null)}
          pending={pending === "confirm"}
          disabled={busy}
        />
      ) : null}
    </section>
  );
}

function EnrichedPreview({
  data,
  onConfirm,
  onCancel,
  pending,
  disabled,
}: {
  data: EnrichResponse;
  onConfirm: () => void;
  onCancel: () => void;
  pending: boolean;
  disabled: boolean;
}) {
  return (
    <div className="mt-4 rounded border border-amber-400 bg-white p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-neutral-800">
          Gemini 웹 검색 결과
        </h3>
        <span className="text-xs text-neutral-500">model: {data.model}</span>
      </div>
      {data.description ? (
        <pre className="mb-3 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-2 text-xs text-neutral-800">
          {data.description}
        </pre>
      ) : (
        <p className="mb-3 text-xs text-neutral-500">
          Gemini 가 유의미한 정보를 찾지 못했습니다.
        </p>
      )}
      {data.citations.length > 0 ? (
        <div className="mb-3">
          <p className="mb-1 text-xs font-medium text-neutral-700">
            근거 ({data.citations.length})
          </p>
          <ul className="space-y-1 pl-4 text-xs text-neutral-700">
            {data.citations.map((c, i) => (
              <li key={i} className="list-disc">
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-700 hover:underline"
                >
                  {c.title || c.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled || !data.description}
          className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-60"
        >
          {pending ? "분류 요청 중..." : "이 설명으로 분류 진행"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={disabled}
          className="rounded border border-neutral-300 px-3 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-60"
        >
          닫기
        </button>
      </div>
    </div>
  );
}

function AnimatedDots() {
  const [n, setN] = useState(1);
  useEffect(() => {
    const id = setInterval(() => setN((x) => (x % 3) + 1), 400);
    return () => clearInterval(id);
  }, []);
  return <span className="inline-block w-4 text-left">{".".repeat(n)}</span>;
}

function extractErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "로그인이 필요합니다.";
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}
