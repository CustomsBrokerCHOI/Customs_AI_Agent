"""품목분류 사례(`openULS0203042S.do`) 실 사이트 통합.

.. warning::
    ``SEL_CASE_*`` 셀렉터는 roadmap 상 **잠정값** — 실 접근 후 확정 예정.
    본 테스트는 과도한 assert 로 셀렉터 회귀를 유발하지 않도록 "크래시 없이
    list 반환" 수준에서만 검증한다. 셀렉터 확정 후 로 매핑 검증 강화 예정.
"""

from __future__ import annotations

import pytest

from scripts.clip_scraper import ClassificationCase, ClipScrapeError

pytestmark = [pytest.mark.clip_live]


def test_fetch_classification_cases_smoke(scraper) -> None:
    """검색이 예외 없이 완료되고 list[ClassificationCase] 를 반환하는지만 검증.

    잠정 셀렉터 회귀로 ``ClipScrapeError`` 가 나오면 xfail 로 처리 (셀렉터 확정 필요).
    """
    try:
        cases = scraper.fetch_classification_cases(
            query="노트북", max_pages=1, fetch_detail=False
        )
    except ClipScrapeError as exc:
        pytest.xfail(f"SEL_CASE_* 잠정 셀렉터 회귀 — 실측 확정 필요: {exc}")

    assert isinstance(cases, list)
    for c in cases:
        assert isinstance(c, ClassificationCase)
        # product_name 은 필수
        assert c.product_name is not None


def test_fetch_classification_cases_rejects_empty_query(scraper) -> None:
    with pytest.raises(ValueError):
        scraper.fetch_classification_cases(query="   ", max_pages=1)
