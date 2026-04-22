"""FAQ(`openULS0206017Q.do`) 실 사이트 통합.

.. warning::
    ``SEL_FAQ_*`` 셀렉터는 roadmap 상 **잠정값** — 실 접근 후 확정 예정.
    셀렉터 회귀는 xfail 로 처리.
"""

from __future__ import annotations

import pytest

from scripts.clip_scraper import ClipScrapeError, FAQEntry

pytestmark = [pytest.mark.clip_live]


def test_fetch_faq_smoke(scraper) -> None:
    try:
        entries = scraper.fetch_faq(query="분류", max_pages=1, fetch_detail=False)
    except ClipScrapeError as exc:
        pytest.xfail(f"SEL_FAQ_* 잠정 셀렉터 회귀 — 실측 확정 필요: {exc}")

    assert isinstance(entries, list)
    for e in entries:
        assert isinstance(e, FAQEntry)
        # question 은 필수
        assert isinstance(e.question, str)


def test_fetch_faq_rejects_empty_query(scraper) -> None:
    with pytest.raises(ValueError):
        scraper.fetch_faq(query="", max_pages=1)
