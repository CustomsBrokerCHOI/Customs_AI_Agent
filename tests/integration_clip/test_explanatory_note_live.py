"""해설서(`openULS0202001Q.do`) 실 사이트 통합.

셀렉터는 확정됨 — 결과 shape 까지 검증한다. HS 8471 (휴대용 자동자료처리기계) 는
한·영 본문이 풍부해 검증 데이터로 적합.
"""

from __future__ import annotations

import pytest

from scripts.clip_scraper import BilingualText, ExplanatoryNote

pytestmark = [pytest.mark.clip_live]


def test_fetch_explanatory_note_8471_returns_bilingual_heading(scraper) -> None:
    note = scraper.fetch_explanatory_note("8471", year="2022")
    assert isinstance(note, ExplanatoryNote)
    assert note.heading == "8471"
    assert note.year == "2022"
    # 통칙/부주/류주/호주 중 최소 하나는 ko 혹은 en 에 본문이 있어야 한다.
    filled = [
        bi
        for bi in (note.general_rule, note.section_note, note.chapter_note, note.heading_note)
        if isinstance(bi, BilingualText) and not bi.is_empty()
    ]
    assert filled, "해설서 4종(통칙/부/류/호) 모두 비어있음 — 셀렉터 회귀 의심"
    # metadata 에 원본 URL 보존
    assert "url" in note.metadata


def test_fetch_explanatory_note_rejects_invalid_heading(scraper) -> None:
    """4자리가 아닌 입력은 ValueError (정상 검증 로직 확인, 네트워크 호출 없음)."""
    with pytest.raises(ValueError):
        scraper.fetch_explanatory_note("84", year="2022")
    with pytest.raises(ValueError):
        scraper.fetch_explanatory_note("abcd", year="2022")
