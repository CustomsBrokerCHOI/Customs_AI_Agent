import type { Verdict } from "@/lib/types";

const STYLES: Record<string, { label: string; cls: string }> = {
  match: {
    label: "일치",
    cls: "bg-emerald-50 text-emerald-800 border-emerald-300",
  },
  mismatch: {
    label: "불일치",
    cls: "bg-red-50 text-red-800 border-red-300",
  },
  uncertain: {
    label: "불확실",
    cls: "bg-amber-50 text-amber-800 border-amber-300",
  },
  unverified: {
    label: "미검증",
    cls: "bg-neutral-100 text-neutral-700 border-neutral-300",
  },
};

export function VerdictBadge({ verdict }: { verdict: Verdict | string }) {
  const s = STYLES[verdict] ?? STYLES.unverified;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${s.cls}`}
    >
      {s.label}
    </span>
  );
}
