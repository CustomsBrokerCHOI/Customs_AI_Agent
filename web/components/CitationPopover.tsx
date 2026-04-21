"use client";

import { useState } from "react";
import type { Citation } from "@/lib/types";

const KIND_LABEL: Record<string, string> = {
  heading_note: "호 해설",
  chapter_note: "류 주",
  section_note: "부 주",
  general_rule: "통칙",
  case: "분류 사례",
};

export function CitationPopover({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  const label = KIND_LABEL[citation.source_kind] ?? citation.source_kind;
  const preview =
    citation.excerpt.length > 60
      ? citation.excerpt.slice(0, 60) + "…"
      : citation.excerpt;

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setOpen(false)}
        className="text-left text-xs text-blue-700 underline underline-offset-2 hover:text-blue-900"
      >
        [{label}
        {citation.heading ? ` · 제${citation.heading}호` : ""}] {preview}
      </button>
      {open ? (
        <div
          role="dialog"
          className="absolute left-0 top-6 z-20 w-96 max-w-[90vw] rounded-md border border-neutral-300 bg-white p-3 shadow-lg"
        >
          <div className="mb-1 flex items-center justify-between text-xs text-neutral-500">
            <span>
              {label}
              {citation.heading ? ` · 제${citation.heading}호` : ""}
            </span>
            {citation.full_text_url ? (
              <a
                href={citation.full_text_url}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 hover:underline"
              >
                원문 보기 ↗
              </a>
            ) : null}
          </div>
          <p className="whitespace-pre-wrap text-sm text-neutral-800">
            {citation.excerpt}
          </p>
        </div>
      ) : null}
    </span>
  );
}
