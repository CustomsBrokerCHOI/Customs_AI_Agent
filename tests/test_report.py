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
    base = dict(
        id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        product_name="M3 맥북에어 13인치",
        description="Apple 제조 휴대용 랩탑 컴퓨터.",
        status="complete",
        reviewed=False,
        accepted_hs_code=None,
        created_at=datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 4, 22, 10, 1, 30, tzinfo=timezone.utc),
        result=_sample_result_dict(),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- render_html_report ----


def test_html_report_contains_core_sections() -> None:
    html = render_html_report(_sample_job())
    # 헤더
    assert "품목분류의견서" in html
    assert "M3 맥북에어 13인치" in html
    assert DRAFT_NOTICE in html
    # 결론 테이블
    assert "8471300000" in html
    assert "휴대용 자동자료처리기계" in html
    assert "85.0%" in html  # confidence 85%
    # 근거 조항
    assert "호 해설" in html
    assert "중량 10kg" in html
    assert "통칙" in html
    # 분류 과정
    assert "① 물품 식별" in html
    assert "⑤ 호·주·해설서 RAG 검증" in html
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


def test_html_report_usage_shown_when_calls_present() -> None:
    html = render_html_report(_sample_job())
    assert "LLM 사용량" in html
    assert "1200" in html
    assert "300" in html


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
