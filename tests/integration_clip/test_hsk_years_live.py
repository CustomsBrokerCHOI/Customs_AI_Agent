"""HSK 연도 드롭다운 감지 (`list_available_hsk_years`) 실 사이트 통합.

``scripts/detect_hsk_version.py`` 가 이 함수로 신 버전 등장을 감지 — 실제 사이트의
드롭다운 구조/값 패턴이 유지되는지 통합 테스트로 확인한다.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.clip_live]


def test_list_available_hsk_years_includes_known_year(scraper) -> None:
    years = scraper.list_available_hsk_years()
    assert isinstance(years, list)
    assert years, "연도 드롭다운 파싱 실패 (빈 리스트)"
    # 2022 는 CLIP 해설서 기본 표기. 이 연도가 목록에 있어야 HSK 2022 이상 지원 정상.
    assert 2022 in years, f"2022 누락: {years}"
    # 오름차순 정렬
    assert years == sorted(years), f"정렬 회귀: {years}"
    # 연도 값 범위 (과도한 이상치 차단)
    for y in years:
        assert 2000 <= y <= 2099, f"비정상 연도: {y}"
