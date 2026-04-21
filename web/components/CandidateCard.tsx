import type { Candidate } from "@/lib/types";
import { CitationPopover } from "./CitationPopover";
import { VerdictBadge } from "./VerdictBadge";

export function CandidateCard({ candidate }: { candidate: Candidate }) {
  const confidencePct = Math.round(candidate.confidence * 1000) / 10;
  return (
    <article className="rounded-lg border bg-white p-4 shadow-sm">
      <header className="flex items-center justify-between gap-4">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-neutral-500">
            #{candidate.rank}
          </span>
          <h3 className="font-mono text-xl font-bold tracking-wider">
            {candidate.hs_code ?? candidate.heading}
          </h3>
          <VerdictBadge verdict={candidate.verdict} />
        </div>
        <div className="text-right">
          <div className="text-xs text-neutral-500">신뢰도</div>
          <div className="font-semibold">{confidencePct.toFixed(1)}%</div>
        </div>
      </header>

      {candidate.name_kr ? (
        <p className="mt-2 text-sm text-neutral-800">{candidate.name_kr}</p>
      ) : null}
      {candidate.name_en ? (
        <p className="text-xs italic text-neutral-500">{candidate.name_en}</p>
      ) : null}

      {candidate.breadcrumb.length > 0 ? (
        <ol className="mt-3 flex flex-wrap items-center gap-1 text-xs text-neutral-600">
          {candidate.breadcrumb.map((step, idx) => (
            <li key={idx} className="flex items-center gap-1">
              <span className="rounded bg-neutral-100 px-2 py-0.5">{step}</span>
              {idx < candidate.breadcrumb.length - 1 ? (
                <span className="text-neutral-400">›</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}

      {candidate.base_tariff_rate ? (
        <p className="mt-3 text-xs text-neutral-600">
          기본관세율: <span className="font-semibold">{candidate.base_tariff_rate}%</span>
        </p>
      ) : null}

      {candidate.citations.length > 0 ? (
        <section className="mt-3 border-t pt-3">
          <h4 className="mb-1 text-xs font-semibold text-neutral-700">
            근거 조항 ({candidate.citations.length})
          </h4>
          <ul className="space-y-1">
            {candidate.citations.map((c, i) => (
              <li key={i}>
                <CitationPopover citation={c} />
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <p className="mt-3 text-xs text-neutral-500">
          근거 조항 없음 — 원문 검증이 불완전합니다. 관세사 직접 확인 필요.
        </p>
      )}
    </article>
  );
}
