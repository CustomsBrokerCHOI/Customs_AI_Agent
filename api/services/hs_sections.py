"""HS CODE 부(Section) 메타데이터 (WCO/HSK 2022 기준).

21 부(Section) 각각의 로마 숫자 번호, 한·영 타이틀, 소속 류(chapter) 범위를
상수로 보유하고 heading 4자리·chapter 2자리 ↔ section roman 양방향 조회를 제공.

Phase 3-B ``search.py`` 에서 섹션 결정 결과로 chapter 필터를 만드는 데 사용.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    roman: str
    title_kr: str
    title_en: str
    chapters: tuple[int, ...]


SECTIONS: tuple[Section, ...] = (
    Section("I", "산 동물; 동물성 생산품", "Live Animals; Animal Products", tuple(range(1, 6))),
    Section("II", "식물성 생산품", "Vegetable Products", tuple(range(6, 15))),
    Section(
        "III",
        "동·식물성 또는 미생물성의 지방·기름 등",
        "Animal, Vegetable or Microbial Fats and Oils",
        (15,),
    ),
    Section(
        "IV",
        "조제식료품; 음료·주류·식초; 담배",
        "Prepared Foodstuffs; Beverages, Spirits and Vinegar; Tobacco",
        tuple(range(16, 25)),
    ),
    Section("V", "광물성 생산품", "Mineral Products", tuple(range(25, 28))),
    Section("VI", "화학공업 생산품", "Products of the Chemical Industries", tuple(range(28, 39))),
    Section(
        "VII",
        "플라스틱·고무와 그 제품",
        "Plastics and Rubber and Articles Thereof",
        (39, 40),
    ),
    Section(
        "VIII",
        "원피·가죽·모피와 그 제품",
        "Raw Hides, Skins, Leather, Furskins and Articles Thereof",
        tuple(range(41, 44)),
    ),
    Section(
        "IX",
        "목재·목탄·코르크·조물제품",
        "Wood, Cork, Straw Plaiting Materials",
        tuple(range(44, 47)),
    ),
    Section(
        "X",
        "펄프·종이·인쇄물",
        "Pulp of Wood; Paper and Paperboard; Printed Matter",
        tuple(range(47, 50)),
    ),
    Section(
        "XI",
        "방직용 섬유와 그 제품",
        "Textiles and Textile Articles",
        tuple(range(50, 64)),
    ),
    Section(
        "XII",
        "신발류·모자류·우산 등",
        "Footwear, Headgear, Umbrellas, Walking-sticks",
        tuple(range(64, 68)),
    ),
    Section(
        "XIII",
        "석·도자제품·유리제품",
        "Articles of Stone, Ceramic, Glass",
        tuple(range(68, 71)),
    ),
    Section(
        "XIV",
        "천연·양식진주, 귀석·반귀석, 귀금속과 그 제품; 모조 신변장식용품",
        "Natural or Cultured Pearls, Precious Stones, Precious Metals; Imitation Jewellery",
        (71,),
    ),
    Section(
        "XV",
        "비금속과 그 제품",
        "Base Metals and Articles of Base Metal",
        tuple(range(72, 84)),
    ),
    Section(
        "XVI",
        "기계류·전기기기와 그 부품",
        "Machinery and Mechanical Appliances; Electrical Equipment",
        (84, 85),
    ),
    Section(
        "XVII",
        "차량·항공기·선박·수송기기",
        "Vehicles, Aircraft, Vessels and Associated Transport Equipment",
        tuple(range(86, 90)),
    ),
    Section(
        "XVIII",
        "광학·정밀·의료기기; 시계·악기",
        "Optical, Photographic, Measuring, Medical Instruments; Clocks; Musical Instruments",
        tuple(range(90, 93)),
    ),
    Section(
        "XIX",
        "무기류·탄약과 그 부속품",
        "Arms and Ammunition; Parts and Accessories",
        (93,),
    ),
    Section(
        "XX",
        "잡품",
        "Miscellaneous Manufactured Articles",
        tuple(range(94, 97)),
    ),
    Section(
        "XXI",
        "예술품·수집품·골동품",
        "Works of Art, Collectors' Pieces and Antiques",
        (97,),
    ),
)


# 역인덱스 — chapter 2자리 → section roman
CHAPTER_TO_SECTION: dict[int, str] = {ch: s.roman for s in SECTIONS for ch in s.chapters}

ROMAN_TO_SECTION: dict[str, Section] = {s.roman: s for s in SECTIONS}


def chapter_to_section(chapter: int) -> str | None:
    """``chapter`` (1~97) → section roman. 범위 밖이면 ``None``."""
    return CHAPTER_TO_SECTION.get(chapter)


def heading_to_section(heading: str) -> str | None:
    """4자리 heading → section roman. 파싱 실패 시 ``None``.

    예: ``"8471"`` → ``"XVI"`` (Chapter 84).
    """
    if not heading or len(heading) < 2:
        return None
    try:
        chapter = int(heading[:2])
    except ValueError:
        return None
    return chapter_to_section(chapter)


def section_by_roman(roman: str) -> Section | None:
    return ROMAN_TO_SECTION.get(roman)


def chapters_from_romans(romans: list[str]) -> set[int]:
    """여러 section roman 의 chapter 집합을 합집합으로 반환."""
    out: set[int] = set()
    for r in romans:
        s = section_by_roman(r)
        if s:
            out.update(s.chapters)
    return out
