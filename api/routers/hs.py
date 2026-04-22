"""HS 부호 마스터 + 관세율 조회."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.models import HSCode
from api.db.session import get_db
from api.deps import CurrentUser
from api.schemas.hs import HSDetail, TariffRow

router = APIRouter(prefix="/hs", tags=["hs"])


@router.get("/{hs_code}", response_model=HSDetail)
async def get_hs_detail(
    hs_code: Annotated[str, Path(min_length=10, max_length=10, pattern=r"^\d{10}$")],
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HSDetail:
    """HS 10자리 부호 마스터 + FTA 별 관세율 전체.

    인증 필요 (관세사 전용 데이터).
    """
    result = await db.execute(
        select(HSCode).options(selectinload(HSCode.tariff_rates)).where(HSCode.hs_code == hs_code)
    )
    hs = result.scalar_one_or_none()
    if hs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HS 부호 없음")

    return HSDetail(
        hs_code=hs.hs_code,
        name_kr=hs.name_kr,
        name_en=hs.name_en,
        heading=hs.heading,
        sub_heading=hs.sub_heading,
        tariff_line=hs.tariff_line,
        qty_unit=hs.qty_unit,
        weight_unit=hs.weight_unit,
        source=hs.source,
        tariff_rates=[TariffRow.model_validate(t) for t in hs.tariff_rates],
    )
