"""HS 마스터 + 세율 응답 스키마."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class TariffRow(BaseModel):
    fta_code: str
    fta_name: str | None
    tax_rate: float | None
    per_unit_tax: float | None
    base_price: float | None
    apply_start: date | None
    apply_end: date | None
    source: str

    model_config = {"from_attributes": True}


class HSDetail(BaseModel):
    hs_code: str
    name_kr: str | None
    name_en: str | None
    heading: str
    sub_heading: str
    tariff_line: str
    qty_unit: str | None
    weight_unit: str | None
    tariff_rates: list[TariffRow]
    source: str

    model_config = {"from_attributes": True}


class SectionInfo(BaseModel):
    roman: str
    title_kr: str
    title_en: str


class HSChild(BaseModel):
    """2/4/6 자리 조회 시 한 단계 하위 항목."""

    code: str
    name_kr: str | None
    name_en: str | None


Level = Literal["chapter", "heading", "subheading", "tariff_line"]


class HSLookupResponse(BaseModel):
    """입력 코드 길이에 따라 동작이 달라지는 통합 응답.

    - 2(류) / 4(호) / 6(소호): ``children`` 에 하위 항목 나열, ``detail`` 은 null
    - 10(세번): ``detail`` 에 마스터+관세율 전체, ``children`` 은 빈 배열
    """

    level: Level
    code: str
    chapter_number: int
    section: SectionInfo | None
    detail: HSDetail | None
    children: list[HSChild]
