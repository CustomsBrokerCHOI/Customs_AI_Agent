"""HSK 버전 감지 + 엔진 hsk_year 전파 단위 테스트."""

from __future__ import annotations

from scripts.clip_scraper import _parse_year_options
from scripts.detect_hsk_version import compare

# ---- _parse_year_options ----


def test_parse_year_options_simple() -> None:
    assert _parse_year_options(["2017", "2022"]) == [2017, 2022]


def test_parse_year_options_strips_suffix_and_dedupes() -> None:
    # "2022년" / "2022.1.1" 등 — 숫자만 뽑아 앞 4자리 사용
    assert _parse_year_options(["2022년", "2022", "2022.01.01"]) == [2022]


def test_parse_year_options_sorts_ascending() -> None:
    assert _parse_year_options(["2027", "2017", "2022"]) == [2017, 2022, 2027]


def test_parse_year_options_rejects_invalid() -> None:
    assert (
        _parse_year_options(["", "abc", "12", "9999999"]) == [9999]
        or _parse_year_options(["", "abc", "12"]) == []
    )
    # 빈/짧은 문자열은 제외
    assert _parse_year_options(["", "abc", "12"]) == []


def test_parse_year_options_out_of_range_filtered() -> None:
    # 범위 1990-2100
    assert _parse_year_options(["1800", "1990", "2100", "2200"]) == [1990, 2100]


def test_parse_year_options_empty() -> None:
    assert _parse_year_options([]) == []


# ---- compare ----


def test_compare_no_clip_years_is_noop() -> None:
    detected, unseen, latest = compare(db_max=2022, clip_years=[])
    assert detected is False
    assert unseen == []
    assert latest is None


def test_compare_db_empty_any_clip_year_triggers_detection() -> None:
    detected, unseen, latest = compare(db_max=None, clip_years=[2017, 2022])
    assert detected is True
    assert unseen == [2017, 2022]
    assert latest == 2022


def test_compare_up_to_date_when_db_covers_latest() -> None:
    detected, unseen, latest = compare(db_max=2022, clip_years=[2017, 2022])
    assert detected is False
    assert unseen == []
    assert latest == 2022


def test_compare_new_version_detected() -> None:
    # 2027 신버전 등장
    detected, unseen, latest = compare(db_max=2022, clip_years=[2017, 2022, 2027])
    assert detected is True
    assert unseen == [2027]
    assert latest == 2027


def test_compare_db_ahead_of_clip_is_false() -> None:
    # CLIP 이 업데이트 지연이고 우리 DB 가 더 앞서면 감지 안 함
    detected, _, latest = compare(db_max=2027, clip_years=[2022])
    assert detected is False
    assert latest == 2022


# ---- classify_engine hsk_year 전파 ----


def test_classify_input_default_hsk_year() -> None:
    from api.services.classify_engine import DEFAULT_HSK_YEAR, ClassifyInput

    inp = ClassifyInput(product_name="x", description="y")
    assert inp.hsk_year == DEFAULT_HSK_YEAR
    assert DEFAULT_HSK_YEAR == 2022


def test_classify_input_custom_hsk_year() -> None:
    from api.services.classify_engine import ClassifyInput

    inp = ClassifyInput(product_name="x", description="y", hsk_year=2027)
    assert inp.hsk_year == 2027


def test_build_sync_bundle_callable_passes_hsk_year(monkeypatch) -> None:
    """_build_sync_bundle_callable 이 fetch_note_bundle 에 hsk_year 를 전달하는지."""
    from api.services import classify_engine as ce
    from api.services.search import HSCandidate

    captured: list[int] = []

    def fake_fetch(session, heading, hsk_year=2022):
        captured.append(hsk_year)
        return None  # bundle 리턴값은 이 테스트에서 중요하지 않음

    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch)

    callable_ = ce._build_sync_bundle_callable(
        [HSCandidate(heading="8471", score=0.9)],
        hsk_year=2027,
    )
    callable_(session=object())

    assert captured == [2027]


def test_build_sync_bundle_callable_default_is_current(monkeypatch) -> None:
    from api.services import classify_engine as ce
    from api.services.search import HSCandidate

    captured: list[int] = []

    def fake_fetch(session, heading, hsk_year=2022):
        captured.append(hsk_year)
        return None

    monkeypatch.setattr(ce, "fetch_note_bundle", fake_fetch)

    callable_ = ce._build_sync_bundle_callable([HSCandidate(heading="8471", score=0.9)])
    callable_(session=object())

    assert captured == [ce.DEFAULT_HSK_YEAR]
