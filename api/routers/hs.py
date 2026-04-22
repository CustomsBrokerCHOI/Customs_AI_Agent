"""HS 부호 마스터 + 관세율 조회.

입력 길이별 동작
---------------
- 2자리 (류): 해당 류(chapter) 소속 4자리 호 목록
- 4자리 (호): 해당 호 소속 6자리 소호 목록
- 6자리 (소호): 해당 소호 소속 10자리 세번 목록
- 10자리 (세번): 마스터 + FTA 별 관세율 상세

DB 가 비어있는 범위는 404 를 반환한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.models import HSCode
from api.db.session import get_db
from api.deps import CurrentUser
from api.schemas.hs import HSChild, HSDetail, HSLookupResponse, SectionInfo, TariffRow
from api.services.hs_sections import chapter_to_section, section_by_roman

router = APIRouter(prefix="/hs", tags=["hs"])

# 2, 4, 6, or 10 자리만 허용
_CODE_PATTERN = r"^\d{2}(\d{2}(\d{2}(\d{4})?)?)?$"


def _section_info(chapter: int) -> SectionInfo | None:
    roman = chapter_to_section(chapter)
    if roman is None:
        return None
    s = section_by_roman(roman)
    if s is None:
        return None
    return SectionInfo(roman=s.roman, title_kr=s.title_kr, title_en=s.title_en)


async def _lookup_tariff_line(code: str, db: AsyncSession) -> HSDetail:
    res = await db.execute(
        select(HSCode).options(selectinload(HSCode.tariff_rates)).where(HSCode.hs_code == code)
    )
    hs = res.scalar_one_or_none()
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


async def _lookup_children(code: str, db: AsyncSession) -> list[HSChild]:
    """2/4/6 자리 prefix 에 대해 한 단계 하위 항목 목록."""
    length = len(code)

    if length == 2:
        # 류 → 소속 4자리 호 distinct (대표 명칭은 min(name_kr))
        stmt = (
            select(
                HSCode.heading.label("code"),
                func.min(HSCode.name_kr).label("name_kr"),
                func.min(HSCode.name_en).label("name_en"),
            )
            .where(HSCode.heading.like(f"{code}%"))
            .group_by(HSCode.heading)
            .order_by(HSCode.heading)
        )
    elif length == 4:
        # 호 → 소속 6자리 소호 distinct
        prefix6 = func.substr(HSCode.hs_code, 1, 6)
        stmt = (
            select(
                prefix6.label("code"),
                func.min(HSCode.name_kr).label("name_kr"),
                func.min(HSCode.name_en).label("name_en"),
            )
            .where(HSCode.heading == code)
            .group_by(prefix6)
            .order_by(prefix6)
        )
    else:  # length == 6
        # 소호 → 하위 10자리 세번 전체
        stmt = (
            select(
                HSCode.hs_code.label("code"),
                HSCode.name_kr.label("name_kr"),
                HSCode.name_en.label("name_en"),
            )
            .where(HSCode.hs_code.like(f"{code}%"))
            .order_by(HSCode.hs_code)
        )

    rows = (await db.execute(stmt)).all()
    return [HSChild(code=r.code, name_kr=r.name_kr, name_en=r.name_en) for r in rows]


_LEVEL_BY_LEN = {2: "chapter", 4: "heading", 6: "subheading", 10: "tariff_line"}


@router.get("/{code}", response_model=HSLookupResponse)
async def get_hs_lookup(
    code: Annotated[str, Path(pattern=_CODE_PATTERN)],
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HSLookupResponse:
    """HS 부호 조회. 2/4/6/10 자리 모두 지원."""
    length = len(code)
    level = _LEVEL_BY_LEN[length]
    chapter_number = int(code[:2])
    section = _section_info(chapter_number)

    if length == 10:
        detail = await _lookup_tariff_line(code, db)
        return HSLookupResponse(
            level=level,
            code=code,
            chapter_number=chapter_number,
            section=section,
            detail=detail,
            children=[],
        )

    children = await _lookup_children(code, db)
    if not children:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 범위에 HS 부호가 없습니다.",
        )
    return HSLookupResponse(
        level=level,
        code=code,
        chapter_number=chapter_number,
        section=section,
        detail=None,
        children=children,
    )
