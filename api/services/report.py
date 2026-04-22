"""Phase 4-C: 분류의견서 (Draft) 렌더러 — HTML + PDF.

설계 결정
---------
- **HTML 우선**: 항상 동작. ``render_html_report`` 는 외부 의존 0.
- **PDF 는 optional dep**: ``weasyprint`` 는 시스템 라이브러리(Cairo/Pango) 요구 →
  lazy import. 미설치 시 ``PDFUnavailable`` 발생, 라우터에서 HTTP 501 로 변환.
- **XSS 방어**: 사용자 입력 (품명/설명/인용 excerpt) 모두 ``html.escape``.
- **구조**: 헤더(품명·작성일·확인상태) → 결론 테이블 → 근거 조항 → 분류 과정
  → 서명란 자리 → Draft 면책.

테스트에서는 ``render_html_report`` 만 검증. PDF 경로는 import fallback 만 확인.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from api.services.classify_engine import DRAFT_NOTICE

logger = logging.getLogger(__name__)


class PDFUnavailable(RuntimeError):
    """weasyprint 미설치 또는 런타임 오류."""


# ---- HTML ----


def _esc(value: Any) -> str:
    """None 및 숫자까지 안전하게 HTML escape."""
    if value is None:
        return ""
    return html.escape(str(value))


def _fmt_dt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return _esc(value)


_KIND_LABEL = {
    "heading_note": "호 해설",
    "chapter_note": "류 주",
    "section_note": "부 주",
    "general_rule": "통칙",
    "case": "분류 사례",
}


def _verdict_label(v: str | None) -> str:
    return {
        "match": "일치",
        "mismatch": "불일치",
        "uncertain": "불확실",
        "unverified": "미검증",
    }.get(v or "", v or "")


def _render_candidates_table(candidates: list[dict]) -> str:
    if not candidates:
        return "<p><em>후보 없음.</em></p>"
    rows: list[str] = []
    for c in candidates:
        hs_code = _esc(c.get("hs_code") or c.get("heading", ""))
        name_kr = _esc(c.get("name_kr") or "")
        verdict = _esc(_verdict_label(c.get("verdict")))
        confidence = c.get("confidence")
        conf_str = (
            f"{round((confidence or 0) * 100, 1)}%" if isinstance(confidence, (int, float)) else ""
        )
        tariff = _esc(c.get("base_tariff_rate") or "—")
        rows.append(
            "<tr>"
            f"<td class='num'>{_esc(c.get('rank', ''))}</td>"
            f"<td class='mono'>{hs_code}</td>"
            f"<td>{name_kr}</td>"
            f"<td>{verdict}</td>"
            f"<td class='num'>{conf_str}</td>"
            f"<td class='num'>{tariff}</td>"
            "</tr>"
        )
    return (
        "<table class='candidates'>"
        "<thead><tr>"
        "<th>#</th><th>HS CODE</th><th>품명</th>"
        "<th>판정</th><th>신뢰도</th><th>기본관세율</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _render_citations(candidates: list[dict]) -> str:
    blocks: list[str] = []
    for c in candidates:
        cites = c.get("citations") or []
        if not cites:
            continue
        heading = _esc(c.get("heading", ""))
        hs = _esc(c.get("hs_code") or heading)
        items: list[str] = []
        for cite in cites:
            kind = cite.get("source_kind", "")
            label = _KIND_LABEL.get(kind, kind)
            head = cite.get("heading")
            head_txt = f" · 제{_esc(head)}호" if head else ""
            excerpt = _esc(cite.get("excerpt", ""))
            items.append(
                f"<li><span class='cite-kind'>{_esc(label)}{head_txt}</span>"
                f"<blockquote>{excerpt}</blockquote></li>"
            )
        blocks.append(
            f"<section class='citations'>"
            f"<h3>#{_esc(c.get('rank', ''))} · {hs}</h3>"
            f"<ul>{''.join(items)}</ul>"
            f"</section>"
        )
    if not blocks:
        return "<p><em>근거 조항이 기록되지 않았습니다. 관세사 직접 검토 필요.</em></p>"
    return "".join(blocks)


def _render_stages(meta: dict | None) -> str:
    if not meta:
        return ""
    stages = meta.get("stages") or {}
    if not isinstance(stages, dict) or not stages:
        return ""
    rows: list[str] = []
    steps: list[tuple[str, str]] = [
        ("input_gate", "① 물품 식별"),
        ("sections", "② 부(Section) 결정"),
        ("search", "③ pgvector 검색"),
        ("verify_gate", "④ 부-류 일치 검증"),
        ("verify_gate_retry", "④-1 필터 해제 재검색"),
        ("deep_verify", "⑤ 호·주·해설서 RAG 검증"),
    ]
    for key, label in steps:
        if key not in stages:
            continue
        rows.append(
            f"<tr><td>{_esc(label)}</td>"
            f"<td><pre class='stage-data'>{_esc(_stage_summary(stages[key]))}</pre></td></tr>"
        )
    usage = meta.get("usage") or {}
    usage_row = ""
    if isinstance(usage, dict) and usage.get("calls"):
        usage_row = (
            f"<p class='usage'>LLM 사용량: {_esc(usage.get('calls'))} 호출 · "
            f"입력 {_esc(usage.get('input_tokens'))} / "
            f"출력 {_esc(usage.get('output_tokens'))} 토큰</p>"
        )
    return (
        "<table class='stages'>"
        "<thead><tr><th>단계</th><th>결과</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        f"{usage_row}"
    )


def _stage_summary(value: Any) -> str:
    """단계 값을 간략 표현."""
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


_CSS = """
@page { size: A4; margin: 2cm; }
body {
  font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  color: #1f2937;
  line-height: 1.5;
  font-size: 11pt;
}
h1 { font-size: 20pt; margin: 0 0 4pt; }
h2 { font-size: 14pt; margin: 20pt 0 6pt; border-bottom: 1pt solid #d1d5db; padding-bottom: 3pt; }
h3 { font-size: 12pt; margin: 12pt 0 4pt; }
.draft {
  background: #fff7ed;
  border: 1pt solid #fb923c;
  color: #9a3412;
  padding: 8pt 10pt;
  border-radius: 4pt;
  margin: 12pt 0;
  font-weight: 600;
}
dl.meta { display: grid; grid-template-columns: 6em 1fr; gap: 2pt 10pt; margin: 10pt 0; }
dl.meta dt { color: #6b7280; }
dl.meta dd { margin: 0; }
table { width: 100%; border-collapse: collapse; margin: 8pt 0; }
th, td { border: 0.5pt solid #d1d5db; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f3f4f6; font-weight: 600; }
td.num { text-align: right; }
td.mono, .mono { font-family: "Courier New", monospace; letter-spacing: 0.02em; }
.cite-kind { font-size: 10pt; color: #6b7280; }
blockquote { margin: 2pt 0 6pt; padding: 4pt 8pt; border-left: 2pt solid #d1d5db; background: #f9fafb; font-size: 10.5pt; }
.notice { background: #fffbeb; border: 1pt solid #fcd34d; padding: 8pt; border-radius: 4pt; }
.signature { margin-top: 30pt; border-top: 1pt solid #d1d5db; padding-top: 12pt; }
.signature .line { display: inline-block; width: 12em; border-bottom: 1pt solid #111827; margin: 0 8pt; }
.disclaimer { font-size: 9.5pt; color: #6b7280; margin-top: 16pt; }
.stage-data { white-space: pre-wrap; font-size: 10pt; font-family: "Courier New", monospace; margin: 0; }
.usage { font-size: 10pt; color: #6b7280; margin: 6pt 0 0; }
.citations h3 { margin-top: 10pt; }
.citations ul { list-style: none; padding: 0; margin: 0; }
.citations li { margin-bottom: 8pt; }
"""


def render_html_report(job: Any) -> str:
    """``ClassifyJob`` ORM 또는 dict 로부터 분류의견서 HTML 생성.

    ``job`` 은 FastAPI ``AsyncSession.get(ClassifyJob, id)`` 결과 또는
    ``result_to_dict`` 로 만든 dict 모두 허용.
    """
    product_name = getattr(job, "product_name", None) or (
        job.get("product_name") if isinstance(job, dict) else ""
    )
    description = getattr(job, "description", None) or (
        job.get("description") if isinstance(job, dict) else ""
    )
    created_at = getattr(job, "created_at", None) or (
        job.get("created_at") if isinstance(job, dict) else None
    )
    completed_at = getattr(job, "completed_at", None) or (
        job.get("completed_at") if isinstance(job, dict) else None
    )
    reviewed = getattr(job, "reviewed", None)
    if reviewed is None and isinstance(job, dict):
        reviewed = job.get("reviewed")
    accepted = getattr(job, "accepted_hs_code", None) or (
        job.get("accepted_hs_code") if isinstance(job, dict) else None
    )

    result = getattr(job, "result", None)
    if result is None and isinstance(job, dict):
        result = job.get("result")
    result = result or {}
    candidates = result.get("candidates") or []
    notice = result.get("notice") or ""
    meta = result.get("meta") or {}

    reviewed_label = "확인 완료" if reviewed else "미확인"
    accepted_html = f"<dt>채택 HS</dt><dd class='mono'>{_esc(accepted)}</dd>" if accepted else ""
    notice_block = f"<p class='notice'>{_esc(notice)}</p>" if notice else ""

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>분류의견서 초안 - {_esc(product_name)}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <h1>품목분류의견서 <span style="font-size:11pt;color:#6b7280;">(Draft)</span></h1>
  <div class="draft">{_esc(DRAFT_NOTICE)}</div>
  <dl class="meta">
    <dt>품명</dt><dd>{_esc(product_name)}</dd>
    <dt>설명</dt><dd>{_esc(description)}</dd>
    <dt>작성일</dt><dd>{_fmt_dt(created_at)}</dd>
    <dt>완료일</dt><dd>{_fmt_dt(completed_at)}</dd>
    <dt>확인</dt><dd>{_esc(reviewed_label)}</dd>
    {accepted_html}
  </dl>
</header>

<section>
  <h2>1. 분류 결론</h2>
  {notice_block}
  {_render_candidates_table(candidates)}
</section>

<section>
  <h2>2. 근거 조항</h2>
  {_render_citations(candidates)}
</section>

<section>
  <h2>3. 분류 과정</h2>
  {_render_stages(meta)}
</section>

<section class="signature">
  <h2>4. 관세사 확인</h2>
  <p>아래 서명으로 본 의견서의 내용을 확인·채택합니다. 서명 이후에도 Draft 표시는 유지되며, 최종 신고 책임은 관세사에게 있습니다.</p>
  <p style="margin-top:14pt;">관세사 성명: <span class="line">&nbsp;</span> (서명) <span class="line">&nbsp;</span></p>
  <p>날짜: <span class="line">&nbsp;</span></p>
</section>

<footer>
  <p class="disclaimer">
    본 문서는 Customs AI Agent 가 생성한 AI 보조 초안입니다. 주·해설서·통칙·판례
    원문은 시스템 DB 의 인용이며, 관세사의 독립적 판단과 대조·확인이 반드시 필요합니다.
    엔진: {_esc(meta.get("engine") or "unknown")}
  </p>
</footer>
</body>
</html>
"""


# ---- PDF ----


def render_pdf_report(html_str: str, *, base_url: str | None = None) -> bytes:
    """HTML → PDF (``weasyprint`` 필요).

    :raises PDFUnavailable: 패키지 미설치 또는 렌더 실패.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise PDFUnavailable(
            "PDF 렌더러(weasyprint) 가 설치되지 않았습니다. "
            "pip install 'weasyprint>=62' 후 시스템 의존(Cairo/Pango) 을 준비하세요."
        ) from exc

    try:
        return HTML(string=html_str, base_url=base_url).write_pdf()
    except Exception as exc:  # noqa: BLE001
        raise PDFUnavailable(f"PDF 렌더 실패: {exc}") from exc


def report_filename(job: Any, ext: str) -> str:
    """다운로드 파일명 — 영숫자 + 품명 일부. ASCII-fallback 포함."""
    pid = str(getattr(job, "id", "")) or (str(job.get("id", "")) if isinstance(job, dict) else "")
    short = pid[:8] if pid else "report"
    return f"classify-{short}.{ext}"
