"""FAQ 행 파싱 단위 테스트 (Playwright 미필요).

``_parse_faq_row`` 는 cells 배열만 받으므로 스크래퍼 인스턴스 없이 테스트하기 위해
``ClipScraper.__new__`` 로 얕은 인스턴스를 만들어 메서드만 호출한다.
"""

from __future__ import annotations

from scripts.clip_scraper import ClipScraper, FAQEntry


def _bare_scraper() -> ClipScraper:
    s = ClipScraper.__new__(ClipScraper)
    return s


# ---- _parse_faq_row ----


def test_parse_faq_row_full_cells() -> None:
    s = _bare_scraper()
    entry = s._parse_faq_row(
        ["FAQ-001", "분류", "노트북은 어떻게 분류하나요?", "2024-01-15"],
        source_url="http://x/faq",
    )
    assert entry is not None
    assert entry.faq_id == "FAQ-001"
    assert entry.category == "분류"
    assert entry.question == "노트북은 어떻게 분류하나요?"
    assert entry.decision_date == "2024-01-15"
    assert entry.source_url == "http://x/faq"
    assert entry.answer is None  # detail 없으면 None


def test_parse_faq_row_missing_tail_cells() -> None:
    """일부 cells 만 있어도 파싱 성공."""
    s = _bare_scraper()
    entry = s._parse_faq_row(
        ["FAQ-002", "원산지"],
        source_url="http://x",
    )
    assert entry is not None
    assert entry.faq_id == "FAQ-002"
    assert entry.category == "원산지"
    # question cells[2] 없음 → non_empty 첫 값으로 대체
    assert entry.question == "FAQ-002"
    assert entry.decision_date is None


def test_parse_faq_row_all_empty_returns_none() -> None:
    s = _bare_scraper()
    assert s._parse_faq_row(["", "", "", ""], source_url="http://x") is None
    assert s._parse_faq_row([], source_url="http://x") is None


def test_parse_faq_row_strips_whitespace() -> None:
    s = _bare_scraper()
    entry = s._parse_faq_row(
        ["  FAQ-003  ", "  관세  ", "  질문  ", " 2024-01 "],
        source_url="http://x",
    )
    assert entry is not None
    assert entry.faq_id == "FAQ-003"
    assert entry.category == "관세"
    assert entry.question == "질문"
    assert entry.decision_date == "2024-01"


def test_parse_faq_row_handles_non_string_cells() -> None:
    s = _bare_scraper()
    entry = s._parse_faq_row(
        [None, 123, "질문", None],  # type: ignore[list-item]
        source_url="http://x",
    )
    assert entry is not None
    # None / 숫자 셀은 빈 문자열 처리
    assert entry.faq_id is None
    assert entry.category is None
    assert entry.question == "질문"


def test_parse_faq_row_positional_wins_over_fallback() -> None:
    """cells[2] 가 빈 문자열로 존재하면 fallback 이 적용되지 않는다
    (_parse_case_row 와 동일한 positional 시맨틱)."""
    s = _bare_scraper()
    entry = s._parse_faq_row(
        ["", "", "", "2024-05-01"],
        source_url="http://x",
    )
    assert entry is not None
    # cells[2] == "" 이므로 question 은 빈 문자열
    assert entry.question == ""
    assert entry.decision_date == "2024-05-01"


def test_parse_faq_row_fallback_only_when_index_missing() -> None:
    """cells 길이가 3 미만이면 non_empty 첫 값이 question 으로 쓰인다."""
    s = _bare_scraper()
    entry = s._parse_faq_row(["", "", ""], source_url="http://x")
    # 모두 빈 문자열 → non_empty 없음 → None
    assert entry is None

    entry2 = s._parse_faq_row(["only"], source_url="http://x")
    assert entry2 is not None
    assert entry2.faq_id == "only"
    # cells[2] 가 아예 없으므로 fallback → non_empty[0] = "only"
    assert entry2.question == "only"


# ---- FAQEntry.as_dict ----


def test_faqentry_as_dict_roundtrip() -> None:
    entry = FAQEntry(
        faq_id="FAQ-01",
        question="q",
        category="cat",
        answer="a",
        hs_code="8471300000",
        decision_date="2024-01-01",
        source_url="http://x",
    )
    d = entry.as_dict()
    assert d["faq_id"] == "FAQ-01"
    assert d["question"] == "q"
    assert d["category"] == "cat"
    assert d["answer"] == "a"
    assert d["hs_code"] == "8471300000"
    assert d["metadata"] == {}


def test_faqentry_as_dict_copies_metadata() -> None:
    meta = {"source": "CLIP"}
    entry = FAQEntry(faq_id="x", question="q", metadata=meta)
    d = entry.as_dict()
    # as_dict 는 얕은 복사
    assert d["metadata"] is not meta
    assert d["metadata"] == {"source": "CLIP"}
