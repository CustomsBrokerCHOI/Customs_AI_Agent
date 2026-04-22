"""관세율표(`openULS0201002Q.do`) 실 사이트 통합.

HS 8471 (자동자료처리기계) 는 기준 34건 세번이 있어 결과 shape 검증에 안정적.
셀렉터는 확정됨.
"""

from __future__ import annotations

import pytest

from scripts.clip_scraper import TariffLine

pytestmark = [pytest.mark.clip_live]


def test_fetch_tariff_schedule_8471_returns_tariff_lines(scraper) -> None:
    lines = scraper.fetch_tariff_schedule("8471")
    assert isinstance(lines, list)
    assert lines, "CLIP 관세율표에서 8471 결과 0건 — 사이트 변경 혹은 셀렉터 회귀"

    # CLIP 은 상위 호·소호 요약 행과 개별 세번 행을 섞어 반환한다 — 일부 행의
    # sub_heading/tariff_line 이 비어 있을 수 있음. 대신 "모두 8471 prefix" +
    # "최소 한 건은 10자리 완성" 조건으로 검증.
    for line in lines:
        assert isinstance(line, TariffLine)
        # 숫자/구분자만 놔두고 prefix '8471' 확인 (점/공백 섞여도 허용)
        digits = "".join(ch for ch in line.heading if ch.isdigit())
        assert digits.startswith("8471") or digits == "", (
            f"heading 이 8471 범위 밖: {line.heading!r}"
        )

    full_hs10 = [ln for ln in lines if len(ln.hs10) == 10 and ln.hs10.isdigit()]
    assert full_hs10, (
        f"10자리 hs10 완성 행 0건 — 셀 매핑 회귀 의심. 첫 행 샘플: {lines[0]}"
    )
    # 완성 행은 적어도 한·영 품명 중 하나를 가져야 한다.
    with_name = [ln for ln in full_hs10 if ln.name_kr or ln.name_en]
    assert with_name, "10자리 완성 행 전체에 품명 누락"


def test_fetch_tariff_schedule_rejects_non_numeric(scraper) -> None:
    with pytest.raises(ValueError):
        scraper.fetch_tariff_schedule("abcd")
