"""HS 마스터 + 세율 응답 스키마."""

from __future__ import annotations

from datetime import date

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
