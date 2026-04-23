"""Phase 4-C 분류의견서 HTML/PDF 렌더러 단위 테스트."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.services.classify_engine import DRAFT_NOTICE
from api.services.report import (
    PDFUnavailable,
    render_html_report,
    render_pdf_report,
    report_filename,
)

# ---- 헬퍼 ----


def _sample_result_dict() -> dict:
    return {
        "candidates": [
            {
                "rank": 1,
                "hs_code": "8471300000",
                "heading": "8471",
                "sub_heading": "30",
                "name_kr": "휴대용 자동자료처리기계",
                "name_en": "Portable ADP",
                "confidence": 0.85,
                "base_tariff_rate": "8",
                "verified": True,
                "verdict": "match",
                "citations": [
                    {
                        "source_kind": "heading_note",
                        "heading": "8471",
                        "excerpt": "휴대용 자동자료처리기계란 중량 10kg 이하인 것",
                    },
                    {
                        "source_kind": "general_rule",
                        "heading": None,
                        "excerpt": "제1호부터 순차로 적용한다.",
                    },
                ],
            },
            {
                "rank": 2,
                "hs_code": "8472000000",
                "heading": "8472",
                "sub_heading": "00",
                "name_kr": "기타 사무용 기계",
                "name_en": "Other office machines",
                "confidence": 0.45,
                "base_tariff_rate": None,
                "verified": False,
                "verdict": "uncertain",
                "citations": [],
            },
        ],
        "notice": "⚠️ 본 결과는 AI 보조 초안(Draft)입니다.",
        "meta": {
            "engine": "real",
            "draft": True,
            "stages": {
                "input_gate": {"confidence": 0.9, "needs_more_info": False},
                "deep_verify": {
                    "processed": 2,
                    "match": 1,
                    "mismatch": 0,
                    "uncertain": 1,
                },
            },
            "usage": {"input_tokens": 1200, "output_tokens": 300, "calls": 4},
        },
    }


def _sample_job(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.UUID("11111111-2222-3333-4444-555555555555"),
        "product_name": "M3 맥북에어 13인치",
        "description": "Apple 제조 휴대용 랩탑 컴퓨터.",
        "status": "complete",
        "reviewed": False,
        "accepted_hs_code": None,
        "created_at": datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 4, 22, 10, 1, 30, tzinfo=timezone.utc),
        "result": _sample_result_dict(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- render_html_report ----


def test_html_report_contains_core_sections() -> None:
    html = render_html_report(_sample_job())
    # 헤더
    assert "품목분류의견서" in html
    assert "M3 맥북에어 13인치" in html
    assert DRAFT_NOTICE in html
    # 결론 테이블 — HS CODE 는 XXXX.XX-XXXX 로 포맷팅되어 노출
    assert "8471.30-0000" in html
    assert "8471300000" not in html  # raw 10자리 표기 금지
    assert "휴대용 자동자료처리기계" in html
    assert "85.0%" in html  # confidence 85%
    # 근거 조항
    assert "호 해설" in html
    assert "중량 10kg" in html
    assert "통칙" in html
    # 분류 의견 — 법적 논증문 + Top-1 HS CODE 포맷 표기
    assert "legal-opinion" in html
    assert "분류 의견" in html
    assert "8471.30-0000" in html  # 결론 라인에 HS 표기
    # 유사 사례 섹션 (session 없어도 안내문은 노출)
    assert "유사 분류 사례" in html
    # 서명란
    assert "관세사 확인" in html
    # Draft 면책
    assert "AI 보조 초안" in html


def test_html_report_reviewed_label() -> None:
    html = render_html_report(_sample_job(reviewed=True))
    assert "확인 완료" in html
    html2 = render_html_report(_sample_job(reviewed=False))
    assert "미확인" in html2


def test_html_report_accepted_hs_shown_only_if_set() -> None:
    html = render_html_report(_sample_job(accepted_hs_code="8471300000"))
    assert "채택 HS" in html
    html2 = render_html_report(_sample_job(accepted_hs_code=None))
    assert "채택 HS" not in html2


def test_html_report_escapes_user_input() -> None:
    evil = '<script>alert("xss")</script>'
    html = render_html_report(_sample_job(product_name=evil, description=evil))
    # raw tag 가 그대로 들어가서는 안 됨
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_report_escapes_citation_excerpts() -> None:
    result = _sample_result_dict()
    result["candidates"][0]["citations"][0]["excerpt"] = "<b>hacked</b>"
    html = render_html_report(_sample_job(result=result))
    assert "<b>hacked</b>" not in html
    assert "&lt;b&gt;hacked&lt;/b&gt;" in html


def test_html_report_handles_missing_result() -> None:
    html = render_html_report(_sample_job(result=None))
    assert "후보 없음" in html
    # 서명란은 여전히 있어야 함
    assert "관세사 확인" in html


def test_html_report_handles_dict_job() -> None:
    """ORM 대신 dict 전달해도 렌더링 가능."""
    job_dict = {
        "id": "abcd",
        "product_name": "TEST",
        "description": "x",
        "result": _sample_result_dict(),
        "reviewed": True,
        "accepted_hs_code": "8471300000",
        "created_at": None,
        "completed_at": None,
    }
    html = render_html_report(job_dict)
    assert "TEST" in html
    assert "확인 완료" in html


def test_html_report_verdict_labels_localized() -> None:
    html = render_html_report(_sample_job())
    assert "일치" in html  # match
    assert "불확실" in html  # uncertain


def test_html_report_no_citations_shows_caveat() -> None:
    result = _sample_result_dict()
    for c in result["candidates"]:
        c["citations"] = []
    html = render_html_report(_sample_job(result=result))
    assert "근거 조항이 기록되지 않았습니다" in html


def test_html_report_notice_block_renders_when_present() -> None:
    html = render_html_report(_sample_job())
    assert "class='notice'" in html or 'class="notice"' in html


def test_html_report_does_not_leak_llm_usage_tokens() -> None:
    """LLM 토큰 사용량은 관세사 의견서에 필요한 정보가 아니므로 노출 금지."""
    html = render_html_report(_sample_job())
    assert "LLM 사용량" not in html
    assert "입력 토큰" not in html
    assert "출력 토큰" not in html


def test_html_report_strips_markdown_from_description() -> None:
    """Gemini 보강 description 에 섞인 마크다운 기호는 의견서 본문에서 제거."""
    from api.services.report import _strip_markdown

    # 단위 함수 검증
    assert _strip_markdown("- **주된 용도**: 운동") == "주된 용도: 운동"
    assert _strip_markdown("## 제목") == "제목"
    assert _strip_markdown("일반 `코드` 텍스트") == "일반 코드 텍스트"
    assert _strip_markdown("[링크](http://x)") == "링크"

    # 전체 리포트에서도 bold·bullet 이 평문화되어 나오는지 확인
    job = _sample_job()
    job.description = (
        "- **주된 용도 및 기능**: 근력 강화\n"
        "- **주요 원재료**: 플라스틱·금속"
    )
    html = render_html_report(job)
    # 원시 마크다운 흔적 없음
    assert "**주된" not in html
    assert "- **" not in html
    # 평문화된 키워드는 그대로 남아 있어야 함
    assert "주된 용도" in html
    assert "근력 강화" in html


def test_html_report_legal_opinion_under_2000_chars() -> None:
    """의견서 본문은 2000자 상한 (OPINION_MAX_CHARS). 초과 시 말줄임."""
    from api.services.report import OPINION_MAX_CHARS, _build_legal_opinion

    job = _sample_job()
    # 아주 긴 description 투입
    huge = "매우 긴 설명. " * 1000
    text = _build_legal_opinion(job.result["candidates"], job.product_name, huge)
    assert len(text) <= OPINION_MAX_CHARS + 1  # 말줄임 "…" 여유 1


# ---- render_pdf_report ----


def test_render_pdf_raises_when_weasyprint_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise ImportError("mock missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(PDFUnavailable, match="weasyprint"):
        render_pdf_report("<html></html>")


def test_render_pdf_wraps_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """weasyprint HTML 렌더가 예외를 내면 PDFUnavailable 로 감싸짐."""
    import sys
    import types as pytypes

    fake_module = pytypes.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, *args, **kwargs):
            pass

        def write_pdf(self):
            raise RuntimeError("simulated render failure")

    fake_module.HTML = _FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", fake_module)

    with pytest.raises(PDFUnavailable, match="PDF 렌더 실패"):
        render_pdf_report("<html></html>")


def test_render_pdf_returns_bytes_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types as pytypes

    fake_module = pytypes.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, string, base_url=None):
            self.string = string

        def write_pdf(self) -> bytes:
            return b"%PDF-1.4 fake"

    fake_module.HTML = _FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", fake_module)

    out = render_pdf_report("<html></html>")
    assert out == b"%PDF-1.4 fake"


# ---- report_filename ----


def test_report_filename_uses_job_short_id() -> None:
    job = _sample_job()
    name = report_filename(job, "pdf")
    assert name.endswith(".pdf")
    # UUID 앞 8자 포함
    assert "11111111" in name


def test_report_filename_handles_empty_id() -> None:
    job = SimpleNamespace(id="")
    name = report_filename(job, "html")
    assert name == "classify-report.html"
