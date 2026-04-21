"use client";

import { useState, type FormEvent } from "react";
import { ApiError, getHsDetail } from "@/lib/api";
import type { HSDetail } from "@/lib/types";

export default function HsLookupPage() {
  const [hsCode, setHsCode] = useState("");
  const [detail, setDetail] = useState<HSDetail | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const code = hsCode.replace(/\D/g, ""); // 숫자만
    if (code.length !== 10) {
      setError("HS 부호는 숫자 10자리로 입력하세요.");
      return;
    }
    setPending(true);
    setError(null);
    setDetail(null);
    try {
      const res = await getHsDetail(code);
      setDetail(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 404 ? "해당 HS 부호가 DB 에 없습니다." : err.message);
      } else {
        setError(err instanceof Error ? err.message : "조회 실패");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">HS 마스터 조회</h1>
        <p className="text-sm text-neutral-600">
          10자리 HS 부호로 품명·FTA 별 관세율을 조회합니다. 데이터 출처: 관세청 UNIPASS.
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
            placeholder="예: 8471300000 또는 8471.30-0000"
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

      {detail ? <HsDetailView detail={detail} /> : null}
    </section>
  );
}

function HsDetailView({ detail }: { detail: HSDetail }) {
  return (
    <article className="space-y-4 rounded-lg border bg-white p-5 shadow-sm">
      <header>
        <h2 className="font-mono text-2xl font-bold tracking-wider">
          {formatHs(detail.hs_code)}
        </h2>
        <p className="mt-1 text-base text-neutral-800">
          {detail.name_kr ?? "—"}
        </p>
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
                        <span className="ml-2 text-xs text-neutral-500">
                          {r.fta_name}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {r.tax_rate ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {r.per_unit_tax ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right">
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
        )}
      </section>

      <p className="text-xs text-neutral-500">출처: {detail.source}</p>
    </article>
  );
}

function formatHs(code: string): string {
  // 1234567890 → 1234.56-7890 (표기만)
  if (code.length !== 10) return code;
  return `${code.slice(0, 4)}.${code.slice(4, 6)}-${code.slice(6)}`;
}
