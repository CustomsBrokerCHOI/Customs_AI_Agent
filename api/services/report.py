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
import re
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from api.db.models import ClassificationCase
from api.services.classify_engine import DRAFT_NOTICE

logger = logging.getLogger(__name__)

SIMILAR_CASE_LIMIT = 5
# 분류 의견서(법적 논증문) 본문의 최대 글자 수. 근거 조항 인용까지 포함한 길이이며,
# 너무 길면 가독성 저하·PDF 1페이지 초과 가능성이 있어 상한을 둠. 초과 시 말줄임.
OPINION_MAX_CHARS = 2000


class PDFUnavailable(RuntimeError):
    """weasyprint 미설치 또는 런타임 오류."""


# ---- HTML ----


def _esc(value: Any) -> str:
    """None 및 숫자까지 안전하게 HTML escape."""
    if value is None:
        return ""
    return html.escape(str(value))


_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*([^\*\n]+)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.M)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def _strip_markdown(text: str) -> str:
    """의견서에 삽입할 때 마크다운 기호를 제거해 평문화.

    Gemini 보강 결과가 과거에 ``- **굵게**`` 형태로 저장된 경우가 있어 리포트
    렌더 시점에도 방어적으로 정리. 신규 호출은 프롬프트 지시로 발생 자체를 억제.
    """
    if not text:
        return text
    t = text
    t = _MD_BOLD_RE.sub(r"\1", t)
    t = _MD_ITALIC_RE.sub(r"\1", t)
    t = _MD_CODE_RE.sub(r"\1", t)
    t = _MD_HEADING_RE.sub("", t)
    t = _MD_BULLET_RE.sub("", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _MULTI_NL_RE.sub("\n\n", t)
    return t.strip()


def _fmt_hs_code(code: Any) -> str:
    """10자리 HS CODE 를 표시용 ``XXXX.XX-XXXX`` 로 포맷.

    6자리는 ``XXXX.XX``, 4자리는 그대로. 파싱 실패 시 원값 반환.
    """
    if code is None:
        return ""
    raw = str(code).strip().replace(".", "").replace("-", "").replace(" ", "")
    if len(raw) == 10 and raw.isdigit():
        return f"{raw[0:4]}.{raw[4:6]}-{raw[6:10]}"
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[0:4]}.{raw[4:6]}"
    return str(code).strip()


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
        hs_display = _esc(_fmt_hs_code(c.get("hs_code") or c.get("heading", "")))
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
            f"<td class='mono'>{hs_display}</td>"
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
        hs_raw = c.get("hs_code") or c.get("heading", "")
        hs = _esc(_fmt_hs_code(hs_raw))
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


def _render_legal_opinion_html(
    candidates: list[dict], product_name: str, description: str
) -> str:
    """의견서 텍스트를 빈 줄 기준 단락 분리해 ``<p>`` 로 렌더.

    ``<strong>`` 는 결론 문장의 HS CODE 강조 용도로만 허용 (사전에 제어 가능한 문자열).
    나머지 사용자 데이터는 HTML escape.
    """
    text = _build_legal_opinion(candidates, product_name, description)
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    rendered: list[str] = []
    for p in paragraphs:
        if p.startswith("—"):
            # 인용 — blockquote 로 표시. "—" 접두 제거 후 escape.
            body = _esc(p[1:].strip())
            rendered.append(f"<blockquote class='opinion-cite'>{body}</blockquote>")
        else:
            # <strong> 는 결론 라인에 한해 사전 삽입된 것 → 파싱 복원.
            # 보안: escape 후 지정 태그만 복원.
            esc = _esc(p)
            esc = esc.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
            rendered.append(f"<p>{esc}</p>")
    return "\n".join(rendered)


def _fetch_similar_cases(
    session: Session | None, heading: str | None, limit: int = SIMILAR_CASE_LIMIT
) -> list[dict]:
    """Top-1 후보 heading 에 해당하는 분류사례 최신 N건.

    session 이 None 이거나 heading 이 없으면 빈 리스트 반환 — 리포트 섹션이
    비표시되어도 무방.
    """
    if session is None or not heading or len(heading) < 4:
        return []
    stmt = (
        select(ClassificationCase)
        .where(ClassificationCase.hs_code.like(f"{heading}%"))
        .order_by(desc(ClassificationCase.decision_date).nulls_last())
        .limit(limit)
    )
    try:
        rows = session.execute(stmt).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("유사 사례 조회 실패 heading=%s: %s", heading, exc)
        return []
    cases: list[dict] = []
    for r in rows:
        cases.append(
            {
                "hs_code": r.hs_code,
                "case_ref": r.case_ref,
                "product_name": r.product_name,
                "decision_date": r.decision_date,
                "source_url": r.source_url,
                "description": r.description,
                "reasoning": r.reasoning,
            }
        )
    return cases


# 유사사례 인라인 본문 길이 상한. 길면 판단이유가 의견서 본문을 압도하므로 말줄임.
_SIMILAR_DESC_MAX = 260
_SIMILAR_REASON_MAX = 500


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    t = _strip_markdown(text).strip()
    if len(t) <= limit:
        return t
    return t[:limit].rstrip() + "…"


def _render_similar_cases(cases: list[dict]) -> str:
    if not cases:
        return (
            "<p><em>유사 분류 사례가 DB 에 없습니다. "
            "품목분류사례 원문 DB(CLIP) 를 직접 조회하실 것을 권합니다.</em></p>"
        )
    blocks: list[str] = []
    for c in cases:
        hs_display = _esc(_fmt_hs_code(c.get("hs_code") or ""))
        case_ref = _esc(c.get("case_ref") or "—")
        product = _esc(c.get("product_name") or "")
        dt = c.get("decision_date")
        date_str = dt.strftime("%Y-%m-%d") if dt else ""
        src = c.get("source_url") or ""
        description_html = _esc(_truncate(c.get("description"), _SIMILAR_DESC_MAX))
        reasoning_html = _esc(_truncate(c.get("reasoning"), _SIMILAR_REASON_MAX))

        meta_parts: list[str] = []
        meta_parts.append(f"<span class='case-ref'>사례번호 {case_ref}</span>")
        if hs_display:
            meta_parts.append(f"<span class='mono'>{hs_display}</span>")
        if date_str:
            meta_parts.append(f"<span>{_esc(date_str)}</span>")
        if src:
            # 링크가 있어도 인라인 본문을 먼저 노출해야 판단이유가 가려지지 않음.
            meta_parts.append(
                f"<a href='{_esc(src)}' target='_blank' rel='noreferrer'>원문 보기 ↗</a>"
            )
        meta_html = "<span class='case-meta-sep'> · </span>".join(meta_parts)

        body_parts: list[str] = []
        if product:
            body_parts.append(f"<p class='case-product'><strong>품명:</strong> {product}</p>")
        if description_html:
            body_parts.append(
                f"<p class='case-description'><strong>설명:</strong> {description_html}</p>"
            )
        if reasoning_html:
            body_parts.append(
                "<div class='case-reasoning'>"
                "<div class='case-reasoning-label'>판단이유</div>"
                f"<blockquote>{reasoning_html}</blockquote>"
                "</div>"
            )
        if not body_parts:
            body_parts.append(
                "<p class='case-empty'><em>상세 설명·판단이유가 기록되지 않음.</em></p>"
            )

        blocks.append(
            "<article class='similar-case'>"
            f"<header class='case-meta'>{meta_html}</header>"
            f"{''.join(body_parts)}"
            "</article>"
        )
    return f"<div class='similar-cases-list'>{''.join(blocks)}</div>"


def _build_legal_opinion(
    candidates: list[dict], product_name: str, description: str
) -> str:
    """Top-1 후보를 중심으로 한 법적 의견서 톤의 분류 논증문.

    "왜 이 HS 코드가 합리적인가" 를 물품 특성 → 부·류 귀속 → 호용어·주 규정 인용 →
    세번 결정 순으로 서술. 단계별 시스템 프로세스 요약이 아니라 관세사가 의견서에
    담을 법적 근거 형식을 따른다.
    """
    if not candidates:
        return (
            "후보 HS CODE 가 도출되지 않아 분류 의견을 제시할 수 없다. "
            "관세사가 품명·설명을 보완하거나 근거 조항을 직접 대조해 판단해야 한다."
        )

    top = candidates[0]
    hs_code_raw = top.get("hs_code") or ""
    hs_code = _fmt_hs_code(hs_code_raw)
    name_kr = (top.get("name_kr") or "").strip()
    heading = top.get("heading") or ""
    breadcrumb = top.get("breadcrumb") or []
    verdict = top.get("verdict") or ""
    citations = top.get("citations") or []

    parts: list[str] = []

    # 1. 물품 특정 — description 에 섞일 수 있는 마크다운 기호 평문화 후 요약.
    desc_clean = _strip_markdown(description or "")
    desc_snippet = " ".join(desc_clean.split())  # 연속 공백·개행 정리
    if len(desc_snippet) > 400:
        desc_snippet = desc_snippet[:400].rstrip() + "…"
    product_sent = f"본건 물품은 '{product_name}'"
    if desc_snippet:
        product_sent += f" 로, {desc_snippet}"
    parts.append(product_sent + " 에 해당하는 제품으로 확인된다.")

    # 2. 분류 경로 (breadcrumb)
    if breadcrumb:
        crumb_text = " › ".join(str(b) for b in breadcrumb)
        parts.append(
            f"물품의 용도·형태·재질·기능 등을 종합적으로 검토한 결과, 관세율표상 {crumb_text} 의 분류 경로가 적절하다고 판단된다."
        )

    # 3. 근거 조항 — matched_clauses
    if citations:
        parts.append("본 분류의 주요 법적 근거는 다음과 같다.")
        for cite in citations[:3]:
            kind_label = _KIND_LABEL.get(cite.get("source_kind", ""), cite.get("source_kind", ""))
            head = cite.get("heading")
            head_txt = f"(제{head}호)" if head else ""
            excerpt = _strip_markdown((cite.get("excerpt") or "").strip())
            if len(excerpt) > 260:
                excerpt = excerpt[:260].rstrip() + "…"
            parts.append(f'— {kind_label}{head_txt}: "{excerpt}"')

    # 4. 결론
    verdict_phrase = {
        "match": "위 호용어 및 주 규정과 합치하는 것으로 확인되므로",
        "uncertain": "호해설과의 일치 여부에 일부 불확실성이 있으나 제출된 근거를 종합할 때",
        "mismatch": "일부 주의 제외 규정과 상충 소지가 있으나 물품 특성의 주된 성격에 비추어",
        "unverified": "원문 substring 검증이 완결되지 않았으나 검색 결과와 도메인 힌트를 종합할 때",
    }.get(verdict, "종합적 판단에 따라")

    if hs_code_raw:
        name_part = f" ({name_kr})" if name_kr else ""
        parts.append(
            f"따라서 본건 물품은 {verdict_phrase} <strong>HS CODE {hs_code}{name_part}</strong> 로 분류하는 것이 합리적이라 사료된다."
        )
    elif heading:
        parts.append(
            f"다만 제{heading}호의 구체적 세번(소호·통계부호) 까지 확정되지 않아, "
            f"{name_kr} 범위에서 관세사의 최종 선택이 필요하다."
        )

    # 5. Draft 면책
    parts.append(
        "본 의견서는 AI 보조 초안으로, 최종 품목분류 확정은 관세사의 독립적 판단과 "
        "근거 조항 원문에 대한 직접적 대조·확인을 통해 이루어져야 한다."
    )

    text = "\n\n".join(parts)
    if len(text) > OPINION_MAX_CHARS:
        text = text[:OPINION_MAX_CHARS].rstrip() + "…"
    return text


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
.notice {
  background: #fffbeb;
  border: 1pt solid #fcd34d;
  padding: 8pt;
  border-radius: 4pt;
  white-space: pre-wrap;
}
.legal-opinion { margin: 8pt 0; text-align: justify; line-height: 1.7; }
.legal-opinion p { margin: 6pt 0; }
.legal-opinion strong { color: #111827; }
.opinion-cite {
  margin: 4pt 0 6pt 12pt;
  padding: 4pt 10pt;
  border-left: 2pt solid #9ca3af;
  background: #f9fafb;
  font-size: 10.5pt;
}
.similar-cases-list { display: block; margin: 8pt 0; }
.similar-case {
  border: 0.5pt solid #d1d5db;
  border-radius: 4pt;
  padding: 8pt 10pt;
  margin: 8pt 0;
  background: #fafafa;
  page-break-inside: avoid;
}
.similar-case .case-meta {
  font-size: 10pt;
  color: #4b5563;
  margin-bottom: 6pt;
  padding-bottom: 4pt;
  border-bottom: 0.3pt dashed #d1d5db;
}
.similar-case .case-meta .case-ref { color: #111827; font-weight: 600; }
.similar-case .case-meta a { color: #1d4ed8; text-decoration: underline; }
.similar-case .case-meta-sep { color: #9ca3af; margin: 0 2pt; }
.similar-case p { margin: 3pt 0; font-size: 10.5pt; }
.similar-case .case-product { color: #111827; }
.similar-case .case-description { color: #1f2937; }
.similar-case .case-reasoning { margin-top: 6pt; }
.similar-case .case-reasoning-label {
  font-size: 9.5pt;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 2pt;
}
.similar-case .case-reasoning blockquote {
  margin: 0;
  font-size: 10pt;
  line-height: 1.55;
}
.similar-case .case-empty { color: #6b7280; }
.signature { margin-top: 30pt; border-top: 1pt solid #d1d5db; padding-top: 12pt; }
.signature .line { display: inline-block; width: 12em; border-bottom: 1pt solid #111827; margin: 0 8pt; }
.disclaimer { font-size: 9.5pt; color: #6b7280; margin-top: 16pt; }
.citations h3 { margin-top: 10pt; }
.citations ul { list-style: none; padding: 0; margin: 0; }
.citations li { margin-bottom: 8pt; }
"""


def render_html_report(job: Any, *, session: Session | None = None) -> str:
    """``ClassifyJob`` ORM 또는 dict 로부터 분류의견서 HTML 생성.

    ``job`` 은 FastAPI ``AsyncSession.get(ClassifyJob, id)`` 결과 또는
    ``result_to_dict`` 로 만든 dict 모두 허용.
    ``session`` 을 주면 Top-1 후보 heading 으로 유사 분류 사례 섹션을 함께 렌더링.
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
    accepted_html = (
        f"<dt>채택 HS</dt><dd class='mono'>{_esc(_fmt_hs_code(accepted))}</dd>"
        if accepted else ""
    )
    notice_block = f"<p class='notice'>{_esc(notice)}</p>" if notice else ""

    # Top-1 heading 기반 유사 사례 (session 있을 때만).
    top_heading = candidates[0].get("heading") if candidates else None
    similar = _fetch_similar_cases(session, top_heading) if top_heading else []

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
    <dt>설명</dt><dd>{_esc(_strip_markdown(description))}</dd>
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
  <h2>3. 분류 의견</h2>
  <div class="legal-opinion">{_render_legal_opinion_html(candidates, product_name, description)}</div>
</section>

<section>
  <h2>4. 유사 분류 사례</h2>
  {_render_similar_cases(similar)}
</section>

<section class="signature">
  <h2>5. 관세사 확인</h2>
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
