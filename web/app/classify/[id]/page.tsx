"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  createClassifyJob,
  enrichProductInfo,
  getClassifyJob,
  getHsLookup,
} from "@/lib/api";
import { CandidateCard } from "@/components/CandidateCard";
import { ReportActions } from "@/components/ReportActions";
import { StageTimeline } from "@/components/StageTimeline";
import { formatHSCode } from "@/lib/hs";
import type {
  Candidate,
  EngineMeta,
  EnrichCitation,
  HSDetail,
  JobStatusResponse,
  TariffRow,
} from "@/lib/types";

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

      {job.result?.candidates && job.result.candidates.length > 0 ? (
        <ResultTabs candidates={job.result.candidates} meta={job.result.meta} />
      ) : job.result?.meta ? (
        <StageTimeline meta={job.result.meta} />
      ) : null}

      {job.status === "complete" ? <ReportActions jobId={job.id} /> : null}

      {job.status === "complete" ? <RefineBlock job={job} /> : null}

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

function mergeDescription(base: string, addendum: string): string {
  const a = (base ?? "").trim();
  const b = (addendum ?? "").trim();
  if (!a) return b;
  if (!b) return a;
  return `${a}\n\n---\n\n${b}`;
}

type EnrichStatus = "loading" | "done" | "error";

function FollowUpBlock({
  questions,
  job,
}: {
  questions: string[];
  job: JobStatusResponse;
}) {
  const router = useRouter();
  const [answers, setAnswers] = useState<string[]>(() =>
    questions.map(() => ""),
  );
  const [enrichStatus, setEnrichStatus] = useState<EnrichStatus>("loading");
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [enrichText, setEnrichText] = useState("");
  const [enrichCitations, setEnrichCitations] = useState<EnrichCitation[]>([]);
  const [enrichModel, setEnrichModel] = useState<string>("");
  const [enrichElapsed, setEnrichElapsed] = useState(0);
  const [submitting, setSubmitting] = useState<null | "combined" | "proceed">(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const enrichStartedRef = useRef(false);

  // 질문 세트가 바뀌면 답변칸도 재초기화 (새 job 으로 이동 시엔 페이지가 리마운트 되므로 거의 안 탐)
  useEffect(() => {
    setAnswers(questions.map(() => ""));
  }, [questions]);

  // job.id 별로 Gemini 자동 1회 실행 — 중복 과금 방어를 위해 ref 가드.
  useEffect(() => {
    if (enrichStartedRef.current) return;
    enrichStartedRef.current = true;

    const start = Date.now();
    setEnrichElapsed(0);
    const timer = setInterval(
      () => setEnrichElapsed(Math.floor((Date.now() - start) / 1000)),
      500,
    );

    enrichProductInfo({
      product_name: job.product_name,
      image_url: job.image_url ?? undefined,
    })
      .then((res) => {
        setEnrichText(res.description ?? "");
        setEnrichCitations(res.citations ?? []);
        setEnrichModel(res.model ?? "");
        setEnrichStatus("done");
      })
      .catch((err) => {
        setEnrichError(extractErrorMessage(err, "Gemini 보강 실패"));
        setEnrichStatus("error");
      })
      .finally(() => clearInterval(timer));

    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id]);

  function updateAnswer(idx: number, value: string) {
    setAnswers((prev) => prev.map((v, j) => (j === idx ? value : v)));
  }

  async function onSubmitCombined() {
    const qaBlock = questions
      .map((q, i) => {
        const a = answers[i]?.trim();
        if (!a) return null;
        return `[보완질문 ${i + 1}] ${q}\n[보완답변 ${i + 1}] ${a}`;
      })
      .filter((s): s is string => Boolean(s))
      .join("\n\n");

    const trimmedEnrich = enrichText.trim();
    const enrichBlock = trimmedEnrich ? `[웹 검색 보강]\n${trimmedEnrich}` : "";

    // 원본 → Gemini 수정본 → 보완답변. 빈 블록은 제외.
    const parts = [job.description.trim(), enrichBlock, qaBlock].filter(
      (s) => s.length > 0,
    );
    const merged = parts.join("\n\n---\n\n");

    setSubmitting("combined");
    setError(null);
    try {
      const res = await createClassifyJob({
        product_name: job.product_name,
        description: merged,
        image_url: job.image_url ?? undefined,
        // 관세사가 보강 내용·보완답변을 검토·확정했으므로 Input Gate 재검증은 생략
        force_classify: true,
      });
      router.push(`/classify/${res.job_id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "재분석 요청 실패"));
      setSubmitting(null);
    }
  }

  async function onProceedAsIs() {
    setSubmitting("proceed");
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
      setSubmitting(null);
    }
  }

  const busy = submitting !== null;
  const anyAnswered = answers.some((a) => a.trim().length > 0);
  const hasEnrich = enrichText.trim().length > 0;
  // Gemini 아직 로딩 중이면 분석 제출은 대기. 답변만으로도 진행할 수 있게 허용.
  const canSubmitCombined =
    (hasEnrich || anyAnswered) && enrichStatus !== "loading";

  return (
    <section className="space-y-4 rounded-lg border border-amber-300 bg-amber-50 p-4">
      <div>
        <h2 className="text-sm font-semibold text-amber-900">
          분류 진행을 위한 추가 정보가 필요합니다
        </h2>
        <p className="mt-1 text-xs text-amber-800">
          Gemini 웹 검색을 자동으로 실행했습니다. 결과를 검토·수정하고 보완
          질문에 답변한 뒤 <strong>이 정보로 분석</strong> 을 누르면 곧바로
          재분류합니다. 빈 칸은 분류에 사용되지 않습니다.
        </p>
      </div>

      <section className="rounded border border-amber-400 bg-white p-3">
        <header className="mb-2 flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-neutral-800">
            Gemini 웹 검색 결과 <span className="font-normal text-xs text-neutral-500">(수정 가능)</span>
          </h3>
          <EnrichStatusIndicator
            status={enrichStatus}
            elapsed={enrichElapsed}
            model={enrichModel}
          />
        </header>

        {enrichStatus === "loading" ? (
          <div className="mb-2 flex items-center gap-2 rounded bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-amber-500 border-t-transparent" />
            <span>
              웹 검색 중<AnimatedDots /> 결과가 준비되면 아래 칸에 자동으로
              채워집니다.
            </span>
          </div>
        ) : null}
        {enrichStatus === "error" ? (
          <p className="mb-2 rounded bg-red-50 px-2 py-1.5 text-xs text-red-700">
            {enrichError ?? "Gemini 보강 실패"} — 답변만으로도 분석을 진행할 수
            있습니다.
          </p>
        ) : null}

        <textarea
          rows={6}
          value={enrichText}
          onChange={(e) => setEnrichText(e.target.value)}
          placeholder={
            enrichStatus === "loading"
              ? "Gemini 응답 대기 중…"
              : "Gemini 가 찾은 내용을 확인·수정하세요. 비워 두면 분류에 사용되지 않습니다."
          }
          disabled={busy}
          className="w-full rounded border border-neutral-300 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500 disabled:bg-neutral-50"
        />

        {enrichCitations.length > 0 ? (
          <div className="mt-2">
            <p className="mb-0.5 text-[11px] font-medium text-neutral-600">
              출처 ({enrichCitations.length})
            </p>
            <ul className="space-y-0.5 pl-4 text-[11px] text-neutral-600">
              {enrichCitations.map((c, i) => (
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
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-amber-900">
          보완 질문 <span className="font-normal text-xs text-amber-700">(답변은 선택 · 빈 칸은 제외됩니다)</span>
        </h3>
        {questions.map((q, i) => (
          <div key={i}>
            <label className="mb-1 block text-sm font-medium text-amber-900">
              보완질문 {i + 1}. {q}
            </label>
            <textarea
              rows={2}
              value={answers[i] ?? ""}
              onChange={(e) => updateAnswer(i, e.target.value)}
              placeholder={`보완답변 ${i + 1} (선택)`}
              disabled={busy}
              className="w-full rounded border border-amber-300 bg-white px-3 py-2 text-sm outline-none focus:border-amber-500 disabled:bg-amber-100/40"
            />
          </div>
        ))}
      </section>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onSubmitCombined}
          disabled={busy || !canSubmitCombined}
          title={
            enrichStatus === "loading"
              ? "Gemini 검색이 끝난 뒤 제출할 수 있습니다."
              : !hasEnrich && !anyAnswered
                ? "Gemini 결과 혹은 보완답변이 필요합니다."
                : undefined
          }
          className="rounded bg-amber-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-60"
        >
          {submitting === "combined" ? "재분석 요청 중..." : "이 정보로 분석"}
        </button>
        <button
          type="button"
          onClick={onProceedAsIs}
          disabled={busy}
          className="rounded border border-amber-400 bg-white px-4 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-60"
        >
          {submitting === "proceed" ? "요청 중..." : "원본 정보로 진행"}
        </button>
      </div>
      {error ? <p className="text-xs text-red-700">{error}</p> : null}
    </section>
  );
}

function EnrichStatusIndicator({
  status,
  elapsed,
  model,
}: {
  status: EnrichStatus;
  elapsed: number;
  model: string;
}) {
  if (status === "loading")
    return (
      <span className="text-[11px] tabular-nums text-neutral-500">
        검색 중… {elapsed}s
      </span>
    );
  if (status === "error")
    return <span className="text-[11px] text-red-600">실패</span>;
  return (
    <span className="text-[11px] text-neutral-500">{model || "gemini"}</span>
  );
}

function RefineBlock({ job }: { job: JobStatusResponse }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [extra, setExtra] = useState("");
  const [imageUrl, setImageUrl] = useState(job.image_url ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit() {
    if (!extra.trim()) {
      setError("보완할 정보를 입력하세요.");
      return;
    }
    setPending(true);
    setError(null);
    const merged = mergeDescription(job.description, `[추가 보완]\n${extra.trim()}`);
    try {
      const res = await createClassifyJob({
        product_name: job.product_name,
        description: merged,
        image_url: imageUrl.trim() ? imageUrl.trim() : undefined,
        // 관세사가 보완 정보를 직접 입력한 상태라 Input Gate 를 다시 거칠 필요 없음.
        force_classify: true,
      });
      router.push(`/classify/${res.job_id}`);
    } catch (err) {
      setError(extractErrorMessage(err, "재판정 요청 실패"));
      setPending(false);
    }
  }

  return (
    <section className="rounded-lg border border-neutral-300 bg-white p-4 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-neutral-800">
            보완 정보로 재판정
          </h2>
          <p className="mt-0.5 text-xs text-neutral-600">
            최종 판정 이후에도 추가 정보를 반영해 새 초안을 만들 수 있습니다.
            기존 설명에 이어 붙인 뒤 곧바로 재분류합니다.
          </p>
        </div>
        <span className="ml-3 text-lg leading-none text-neutral-500">
          {open ? "−" : "+"}
        </span>
      </button>
      {open ? (
        <div className="mt-4 space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-neutral-800">
              추가 보완 정보
            </span>
            <textarea
              rows={5}
              maxLength={4000}
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              placeholder="재질·용도·제조 방식·치수·포장 단위 등 이전 판정에서 부족했던 정보를 추가하세요."
              disabled={pending}
              className="w-full rounded border px-3 py-2 text-sm outline-none focus:border-neutral-500 disabled:bg-neutral-50"
            />
            <p className="mt-1 text-xs text-neutral-500">
              {extra.length} / 4000
            </p>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-neutral-800">
              사진 URL (선택)
            </span>
            <input
              type="url"
              maxLength={500}
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              placeholder="https://…"
              disabled={pending}
              className="w-full rounded border px-3 py-2 text-sm outline-none focus:border-neutral-500 disabled:bg-neutral-50"
            />
          </label>
          {error ? (
            <p className="text-xs text-red-700">{error}</p>
          ) : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setExtra("");
                setError(null);
              }}
              disabled={pending}
              className="rounded px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100 disabled:opacity-60"
            >
              닫기
            </button>
            <button
              type="button"
              onClick={onSubmit}
              disabled={pending || !extra.trim()}
              className="rounded bg-neutral-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-60"
            >
              {pending ? "재판정 요청 중..." : "보완 정보로 재판정"}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

type ResultTabKey = "result" | "tariff";

function ResultTabs({
  candidates,
  meta,
}: {
  candidates: Candidate[];
  meta?: EngineMeta;
}) {
  const [tab, setTab] = useState<ResultTabKey>("result");
  const tariffEligible = candidates.filter((c) => isTenDigit(c.hs_code));
  const tariffBadge = tariffEligible.length > 0 ? tariffEligible.length : undefined;

  return (
    <section className="space-y-4">
      <div role="tablist" className="flex gap-1 border-b">
        <TabButton
          active={tab === "result"}
          onClick={() => setTab("result")}
          label="분류 결과"
        />
        <TabButton
          active={tab === "tariff"}
          onClick={() => setTab("tariff")}
          label="관세율 상세"
          badge={tariffBadge}
        />
      </div>

      {tab === "result" ? (
        <div className="space-y-6">
          {meta ? <StageTimeline meta={meta} /> : null}
          <section>
            <h2 className="mb-3 text-lg font-semibold">후보 HS CODE</h2>
            <ul className="grid gap-3">
              {candidates.map((c) => (
                <li key={`${c.rank}-${c.heading}`}>
                  <CandidateCard candidate={c} />
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : (
        <TariffTabContent candidates={candidates} />
      )}
    </section>
  );
}

function TabButton({
  active,
  onClick,
  label,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  badge?: number;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={[
        "inline-flex items-center gap-2 rounded-t px-4 py-2 text-sm font-medium transition-colors",
        active
          ? "border border-b-white bg-white text-neutral-900 shadow-sm -mb-px"
          : "border border-transparent text-neutral-500 hover:text-neutral-800",
      ].join(" ")}
    >
      <span>{label}</span>
      {typeof badge === "number" ? (
        <span
          className={[
            "rounded-full px-1.5 text-[10px] font-semibold",
            active ? "bg-neutral-900 text-white" : "bg-neutral-200 text-neutral-700",
          ].join(" ")}
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}

function isTenDigit(code: string | null | undefined): boolean {
  if (!code) return false;
  return code.replace(/\D/g, "").length === 10;
}

interface TariffState {
  loading: boolean;
  detail?: HSDetail | null;
  error?: string;
}

function TariffTabContent({ candidates }: { candidates: Candidate[] }) {
  const targets = useMemo(
    () =>
      candidates.filter((c): c is Candidate & { hs_code: string } =>
        isTenDigit(c.hs_code),
      ),
    [candidates],
  );
  const targetKey = targets.map((c) => c.hs_code).join("|");

  const [state, setState] = useState<Record<string, TariffState>>({});

  useEffect(() => {
    if (targets.length === 0) return;
    let cancelled = false;
    setState((prev) => {
      const next = { ...prev };
      for (const c of targets) {
        if (!next[c.hs_code]) next[c.hs_code] = { loading: true };
      }
      return next;
    });
    (async () => {
      await Promise.all(
        targets.map(async (c) => {
          const code = c.hs_code;
          try {
            const res = await getHsLookup(code);
            if (cancelled) return;
            setState((prev) => ({
              ...prev,
              [code]: { loading: false, detail: res.detail ?? null },
            }));
          } catch (err) {
            if (cancelled) return;
            const msg =
              err instanceof ApiError
                ? err.status === 404
                  ? "DB 에 세율 정보가 없습니다."
                  : err.message
                : err instanceof Error
                  ? err.message
                  : "조회 실패";
            setState((prev) => ({
              ...prev,
              [code]: { loading: false, error: msg },
            }));
          }
        }),
      );
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey]);

  if (targets.length === 0) {
    return (
      <div className="rounded border border-neutral-200 bg-neutral-50 px-4 py-6 text-sm text-neutral-600">
        10자리 세번이 확정된 후보가 없어 관세율 조회를 건너뜁니다. 후보의 HS
        CODE 가 10자리로 특정되면 기본세율·FTA 협정세율·조정·할당·단위세 등이
        모두 여기에 표시됩니다.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-neutral-500">
        UNIPASS 관세율(retrieveTrrt) 기준 — 기본세율(A), FTA 협정세율, 조정·할당,
        단위세·기준가·적용기간을 모두 표시합니다.
      </p>
      {targets.map((c) => (
        <TariffCard
          key={c.hs_code}
          candidate={c}
          state={state[c.hs_code] ?? { loading: true }}
        />
      ))}
    </div>
  );
}

function TariffCard({
  candidate,
  state,
}: {
  candidate: Candidate & { hs_code: string };
  state: TariffState;
}) {
  return (
    <section className="rounded-lg border bg-white p-4 shadow-sm">
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-semibold text-neutral-500">
              #{candidate.rank}
            </span>
            <h3 className="font-mono text-lg font-bold tracking-wider">
              {formatHSCode(candidate.hs_code)}
            </h3>
          </div>
          {candidate.name_kr ? (
            <p className="mt-1 text-sm text-neutral-700">{candidate.name_kr}</p>
          ) : null}
        </div>
        {candidate.base_tariff_rate ? (
          <div className="text-right text-xs text-neutral-600">
            <div>요약 기본세율</div>
            <div className="font-semibold text-neutral-900">
              {candidate.base_tariff_rate}%
            </div>
          </div>
        ) : null}
      </header>

      {state.loading ? (
        <p className="text-sm text-neutral-500">관세율 조회 중…</p>
      ) : state.error ? (
        <p className="text-sm text-red-700">조회 실패: {state.error}</p>
      ) : !state.detail || state.detail.tariff_rates.length === 0 ? (
        <p className="text-sm text-neutral-500">등록된 세율이 없습니다.</p>
      ) : (
        <TariffRatesTable rates={state.detail.tariff_rates} />
      )}
    </section>
  );
}

function TariffRatesTable({ rates }: { rates: TariffRow[] }) {
  return (
    <div className="overflow-hidden rounded border">
      <table className="min-w-full divide-y divide-neutral-200 text-sm">
        <thead className="bg-neutral-50 text-xs uppercase tracking-wider text-neutral-600">
          <tr>
            <th className="px-3 py-1.5 text-left">구분</th>
            <th className="px-3 py-1.5 text-right">세율(%)</th>
            <th className="px-3 py-1.5 text-right">단위세</th>
            <th className="px-3 py-1.5 text-right">기준가</th>
            <th className="px-3 py-1.5 text-left">적용기간</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {rates.map((r, i) => (
            <tr key={`${r.fta_code}-${i}`}>
              <td className="px-3 py-1.5">
                <span className="font-mono text-xs font-medium text-neutral-800">
                  {r.fta_code}
                </span>
                {r.fta_name ? (
                  <span className="ml-2 text-xs text-neutral-500">
                    {r.fta_name}
                  </span>
                ) : null}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {r.tax_rate ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {r.per_unit_tax ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {r.base_price ?? "—"}
              </td>
              <td className="px-3 py-1.5 text-xs text-neutral-600">
                {r.apply_start ?? "—"}
                {r.apply_end ? ` ~ ${r.apply_end}` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
