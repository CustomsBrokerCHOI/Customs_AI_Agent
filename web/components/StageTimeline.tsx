import type { EngineMeta } from "@/lib/types";

// EngineResult.meta.stages 를 시각화. 각 단계 통과 여부·주요 수치를 라벨링.

interface Stage {
  key: string;
  label: string;
  summary: (raw: unknown) => string | null;
}

const STAGES: Stage[] = [
  {
    key: "input_gate",
    label: "① 물품 식별",
    summary: (raw) => {
      const s = raw as { confidence?: number; needs_more_info?: boolean } | undefined;
      if (!s) return null;
      if (s.needs_more_info) return "추가 정보 필요";
      if (typeof s.confidence === "number")
        return `신뢰도 ${(s.confidence * 100).toFixed(0)}%`;
      return "완료";
    },
  },
  {
    key: "sections",
    label: "② 부(Section) 결정",
    summary: (raw) => {
      const arr = raw as Array<{ roman: string; confidence: number }> | undefined;
      if (!arr || arr.length === 0) return null;
      return arr
        .map((s) => `${s.roman} (${Math.round(s.confidence * 100)}%)`)
        .join(", ");
    },
  },
  {
    key: "search",
    label: "③ pgvector 검색",
    summary: (raw) => {
      const s = raw as
        | { note_hits?: number; case_hits?: number; candidates?: number }
        | undefined;
      if (!s) return null;
      return `해설서 ${s.note_hits ?? 0}건 · 사례 ${s.case_hits ?? 0}건 → 후보 ${s.candidates ?? 0}`;
    },
  },
  {
    key: "verify_gate",
    label: "④ 부-류 일치 검증",
    summary: (raw) => {
      const s = raw as
        | { verified?: number; rejected?: number; should_re_determine?: boolean }
        | undefined;
      if (!s) return null;
      const base = `통과 ${s.verified ?? 0} / 기각 ${s.rejected ?? 0}`;
      return s.should_re_determine ? `${base} · 재검색 필요` : base;
    },
  },
  {
    key: "verify_gate_retry",
    label: "④-1 필터 해제 재검색",
    summary: (raw) => {
      const s = raw as { bypassed?: boolean; candidates?: number } | undefined;
      if (!s) return null;
      return s.bypassed ? `게이트 우회 · 후보 ${s.candidates ?? 0}` : null;
    },
  },
  {
    key: "deep_verify",
    label: "⑤ 호·주·해설서 RAG 검증",
    summary: (raw) => {
      const s = raw as
        | {
            processed?: number;
            match?: number;
            mismatch?: number;
            uncertain?: number;
          }
        | undefined;
      if (!s) return null;
      return `일치 ${s.match ?? 0} · 불일치 ${s.mismatch ?? 0} · 불확실 ${s.uncertain ?? 0} (${s.processed ?? 0}개 검증)`;
    },
  },
];

export function StageTimeline({ meta }: { meta?: EngineMeta }) {
  const stages = meta?.stages;
  if (!stages) return null;

  const rendered = STAGES.map((stage) => ({
    ...stage,
    value: (stages as Record<string, unknown>)[stage.key],
  })).filter((s) => s.value !== undefined);

  if (rendered.length === 0) return null;

  return (
    <section className="rounded-lg border bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-neutral-700">분류 과정</h2>
      <ol className="space-y-2">
        {rendered.map((s) => (
          <li key={s.key} className="flex items-start gap-3 text-sm">
            <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-emerald-500" />
            <div className="flex-1">
              <div className="font-medium text-neutral-800">{s.label}</div>
              <div className="text-xs text-neutral-600">
                {s.summary(s.value) ?? "완료"}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {meta?.usage ? (
        <p className="mt-4 border-t pt-3 text-xs text-neutral-500">
          LLM 사용량: {meta.usage.calls ?? 0} 회 호출 · 입력{" "}
          {meta.usage.input_tokens?.toLocaleString() ?? 0} / 출력{" "}
          {meta.usage.output_tokens?.toLocaleString() ?? 0} 토큰
        </p>
      ) : null}
    </section>
  );
}
