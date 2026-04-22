"use client";

import { useState, type FormEvent } from "react";
import { ApiError, getHsLookup } from "@/lib/api";
import type { HSChild, HSDetail, HSLookupResponse } from "@/lib/types";

const VALID_LENGTHS = [2, 4, 6, 10] as const;

export default function HsLookupPage() {
  const [hsCode, setHsCode] = useState("");
  const [result, setResult] = useState<HSLookupResponse | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function lookup(raw: string) {
    const code = raw.replace(/\D/g, "");
    if (!VALID_LENGTHS.includes(code.length as (typeof VALID_LENGTHS)[number])) {
      setError("HS 부호는 2(류) · 4(호) · 6(소호) · 10(세번) 자리로 입력하세요.");
      return;
    }
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const res = await getHsLookup(code);
      setResult(res);
      setHsCode(code);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 404 ? "해당 범위에 HS 부호가 없습니다." : err.message);
      } else {
        setError(err instanceof Error ? err.message : "조회 실패");
      }
    } finally {
      setPending(false);
    }
  }

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void lookup(hsCode);
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">HS 마스터 조회</h1>
        <p className="text-sm text-neutral-600">
          2(류) · 4(호) · 6(소호) · 10(세번) 자리로 조회 가능. 데이터 출처: 관세청 UNIPASS.
        </p>
      </div>

      <form
        onSubmit={onSubmit}
        className="flex items-end gap-3 rounded-lg border bg-white p-4 shadow-sm"
      >
        <label className="flex-1">
          <span className="mb-1 block text-sm font-medium text-neutral-800">HS 부호</span>
          <input
            type="text"
            inputMode="numeric"
            maxLength={14}
            value={hsCode}
            onChange={(e) => setHsCode(e.target.value)}
            placeholder="예: 21 / 2103 / 210310 / 2103101000"
            className="w-full rounded border px-3 py-2 font-mono text-sm outline-none focus:border-neutral-500"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {pending ? "조회 중..." : "조회"}
        </button>
      </form>

      {error ? (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {result ? <LookupView result={result} onDrill={lookup} /> : null}
    </section>
  );
}

function LookupView({
  result,
  onDrill,
}: {
  result: HSLookupResponse;
  onDrill: (code: string) => void;
}) {
  return (
    <article className="space-y-4 rounded-lg border bg-white p-5 shadow-sm">
      <LookupHeader result={result} onDrill={onDrill} />
      {result.level === "tariff_line" && result.detail ? (
        <HsDetailView detail={result.detail} />
      ) : (
        <HsChildrenView level={result.level} children={result.children} onDrill={onDrill} />
      )}
    </article>
  );
}

const LEVEL_LABEL: Record<HSLookupResponse["level"], string> = {
  chapter: "류 (2자리)",
  heading: "호 (4자리)",
  subheading: "소호 (6자리)",
  tariff_line: "세번 (10자리)",
};

function LookupHeader({
  result,
  onDrill,
}: {
  result: HSLookupResponse;
  onDrill: (code: string) => void;
}) {
  const crumbs = buildBreadcrumbs(result.code);
  return (
    <header className="space-y-2">
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        <span className="rounded bg-neutral-100 px-2 py-0.5 font-medium text-neutral-700">
          {LEVEL_LABEL[result.level]}
        </span>
        {result.section ? (
          <span>
            제{result.section.roman}부 · {result.section.title_kr}
          </span>
        ) : null}
      </div>
      <h2 className="font-mono text-2xl font-bold tracking-wider">
        {formatHs(result.code)}
      </h2>
      {crumbs.length > 1 ? (
        <nav className="text-xs text-neutral-600">
          {crumbs.map((c, i) => (
            <span key={c.code}>
              {i > 0 ? <span className="mx-1 text-neutral-400">›</span> : null}
              {c.code === result.code ? (
                <span className="font-mono">{c.label}</span>
              ) : (
                <button
                  type="button"
                  onClick={() => onDrill(c.code)}
                  className="font-mono text-blue-600 hover:underline"
                >
                  {c.label}
                </button>
              )}
            </span>
          ))}
        </nav>
      ) : null}
    </header>
  );
}

function HsChildrenView({
  level,
  children,
  onDrill,
}: {
  level: HSLookupResponse["level"];
  children: HSChild[];
  onDrill: (code: string) => void;
}) {
  const childLabel =
    level === "chapter" ? "호 (4자리)" : level === "heading" ? "소호 (6자리)" : "세번 (10자리)";
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold text-neutral-800">
        하위 {childLabel} — {children.length}건
      </h3>
      {children.length === 0 ? (
        <p className="text-sm text-neutral-500">하위 항목이 없습니다.</p>
      ) : (
        <ul className="divide-y rounded border">
          {children.map((c) => (
            <li key={c.code}>
              <button
                type="button"
                onClick={() => onDrill(c.code)}
                className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-neutral-50"
              >
                <span className="font-mono text-sm font-medium text-blue-700">
                  {formatHs(c.code)}
                </span>
                <span className="flex-1 text-sm text-neutral-700">
                  {c.name_kr ?? "—"}
                  {c.name_en ? (
                    <span className="ml-2 text-xs italic text-neutral-400">{c.name_en}</span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-neutral-500">
        하위 명칭은 DB 내 10자리 세번 기준 대표값입니다.
      </p>
    </section>
  );
}

function HsDetailView({ detail }: { detail: HSDetail }) {
  return (
    <>
      <header>
        <p className="text-base text-neutral-800">{detail.name_kr ?? "—"}</p>
        {detail.name_en ? (
          <p className="text-sm italic text-neutral-500">{detail.name_en}</p>
        ) : null}
      </header>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-neutral-500">호</dt>
          <dd className="font-mono">{detail.heading}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">소호</dt>
          <dd className="font-mono">{detail.sub_heading}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">세번</dt>
          <dd className="font-mono">{detail.tariff_line}</dd>
        </div>
        <div>
          <dt className="text-xs text-neutral-500">단위</dt>
          <dd>
            {[detail.qty_unit, detail.weight_unit].filter(Boolean).join(" / ") || "—"}
          </dd>
        </div>
      </dl>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-neutral-800">관세율 (FTA 별)</h3>
        {detail.tariff_rates.length === 0 ? (
          <p className="text-sm text-neutral-500">등록된 세율이 없습니다.</p>
        ) : (
          <div className="overflow-hidden rounded border">
            <table className="min-w-full divide-y divide-neutral-200 text-sm">
              <thead className="bg-neutral-50 text-xs uppercase tracking-wider text-neutral-600">
                <tr>
                  <th className="px-3 py-1.5 text-left">FTA</th>
                  <th className="px-3 py-1.5 text-right">세율(%)</th>
                  <th className="px-3 py-1.5 text-right">단위세</th>
                  <th className="px-3 py-1.5 text-right">기준가</th>
                  <th className="px-3 py-1.5 text-left">적용기간</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100">
                {detail.tariff_rates.map((r, i) => (
                  <tr key={i}>
                    <td className="px-3 py-1.5">
                      <span className="font-mono">{r.fta_code}</span>
                      {r.fta_name ? (
                        <span className="ml-2 text-xs text-neutral-500">{r.fta_name}</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5 text-right">{r.tax_rate ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">{r.per_unit_tax ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">{r.base_price ?? "—"}</td>
                    <td className="px-3 py-1.5 text-xs text-neutral-600">
                      {r.apply_start ?? "—"}
                      {r.apply_end ? ` ~ ${r.apply_end}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-xs text-neutral-500">출처: {detail.source}</p>
    </>
  );
}

function formatHs(code: string): string {
  if (code.length === 10) return `${code.slice(0, 4)}.${code.slice(4, 6)}-${code.slice(6)}`;
  if (code.length === 6) return `${code.slice(0, 4)}.${code.slice(4)}`;
  return code;
}

function buildBreadcrumbs(code: string): { code: string; label: string }[] {
  const crumbs: { code: string; label: string }[] = [];
  if (code.length >= 2) crumbs.push({ code: code.slice(0, 2), label: `제${code.slice(0, 2)}류` });
  if (code.length >= 4) crumbs.push({ code: code.slice(0, 4), label: code.slice(0, 4) });
  if (code.length >= 6)
    crumbs.push({ code: code.slice(0, 6), label: `${code.slice(0, 4)}.${code.slice(4, 6)}` });
  if (code.length === 10) crumbs.push({ code, label: formatHs(code) });
  return crumbs;
}
