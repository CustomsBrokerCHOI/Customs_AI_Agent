"""Sprint B 첫 발 — 관세사 수동 교정 (Food Solution 류 우선).

이 스크립트는 관세사 (15년차) 가 제시한 inclusion/exclusion 문구와 경합호 구분논리를
hs_nodes / hs_sibling_discriminators 에 직접 적재한다. Curly Kale Powder 오분류
(→ 1106 으로 잘못 판정) 회귀 케이스가 모티브.

참고:
- 제07류 / 제11류 / 제20류 / 제21류 를 한 번에 다룬다. 이들은 과채·곡물·조제식품의
  공통 "Powder 함정" 덫에 걸리는 슈퍼푸드 분말 류.
- 기존 자동 파싱 결과 위에 수동 문구를 *추가* (기존 배열에 병합, 중복 제거).
- 재실행 멱등.

사용:
    python -m scripts.manual_curate_hs_nodes
    python -m scripts.manual_curate_hs_nodes --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import HSNode, HSSiblingDiscriminator

load_dotenv()
logger = logging.getLogger(__name__)

VERSION = "HSK-2022"


# -------- 관세사 제시 수동 교정 데이터 --------

# chapter 별 수동 inclusion/exclusion 추가. 자동 파싱 결과와 병합.
MANUAL_CURATION: dict[str, dict] = {
    "07": {
        "essential_character": "원료",  # 관세사: 원료(잎/줄기/뿌리채소)로 류 결정
        "inclusion_keywords": [
            # 호 0712 용어 원문 (관세사 제시) — 이게 핵심. positive boost 에 쓰임.
            "건조한 채소",
            "채소의 가루",
            "절단한 채소",
            "잘게 자른 채소",
            "쪼개거나 부순 채소",
            # 슈퍼푸드 분말 대표 원료
            "케일",
            "시금치",
            "비트",
            "모링가",
            "브로콜리",
        ],
        "exclusion_keywords": [
            # 반대편: 추가 가공·조제가 가해진 것은 여기가 아님
            "추가 조제된 것은 제외",
            "양념을 첨가한 것은 제2005호",
        ],
        "notes_append": (
            "\n[관세사 수동 메모] 잎·줄기·뿌리 채소를 세척·건조·분쇄만 한 것은 "
            "호 0712 의 '건조한 채소 (가루 상태 포함)' 범위. 양념·조미료 첨가는 2005, "
            "식이보충제·캡슐·혼합분말 형태는 2106."
        ),
    },
    "11": {
        "essential_character": "원료",
        "inclusion_keywords": [
            # 11류는 곡물(제10류)·건조채두(0713)·견과(제08류) 가공품만
            "곡물의 가루",
            "곡분",
            "거친 가루",
            "펠릿",
            "전분",
            "밀가루",
            "쌀가루",
            "옥수수 가루",
        ],
        "exclusion_keywords": [
            # 관세사 핵심 조문: 제11류 주 1(가)
            "제0712호의 건조채소로 만든 가루는 제11류 제외",
            "0712호 건조채소 가루 제외 (제11류 주1(가))",
            "곡물·채두·견과 외 원료 제외",
            "채소 분말 제외",
            "잎채소 가루 제외",
        ],
        "notes_append": (
            "\n[관세사 수동 메모] 원료가 곡물(제10류)·건조채두(0713)·견과(제08류) 인 경우만 "
            "제11류 입장권. 케일·시금치·비트·모링가 등 잎·줄기·뿌리채소 분말은 **제11류 주 1(가)** 에 "
            "의해 명시 제외 → 0712로 분류."
        ),
    },
    "20": {
        "essential_character": "가공도",
        "inclusion_keywords": [
            "조제한 채소",
            "조리한 채소",
            "설탕·양념을 첨가한 채소",
            "식초·아세트산으로 조제",
            "냉동한 조리채소",
        ],
        "exclusion_keywords": [
            "세척·건조만 한 단순 채소는 제외 (0712호)",
            "채소 가루로 추가 가공 없는 것은 제외",
        ],
    },
    "21": {
        "essential_character": "용도",
        "inclusion_keywords": [
            "조제식료품",
            "식이보충제",
            "프로틴 파우더",
            "복합 분말 믹스",
            "양념·소스·조미료",
        ],
        "exclusion_keywords": [
            "단일 원료 건조분말은 제외 (원료 호 우선)",
            "가공 없는 단순 채소 분말은 제외 (0712호)",
        ],
    },
}


# 경합호 구분논리. (node_code, vs_code, discriminator).
# node_code 는 해당 heading/chapter 의 code. Sprint A 는 chapter 수준이라 chapter code 사용.
MANUAL_DISCRIMINATORS: list[tuple[str, str, str, str]] = [
    (
        "07",
        "11",
        "원료가 잎·줄기·뿌리채소면 0712, 곡물(제10류)·건조채두(0713)·견과(제08류)면 1106. 형태(powder)로 판정 금지.",
        "제11류 주 1(가) + 0712 호 용어",
    ),
    (
        "07",
        "20",
        "세척·건조·분쇄만 한 것은 0712. 양념·설탕·식초 첨가 또는 조리(열처리)된 것은 2005/2008.",
        "통칙 1 + 제20류 주",
    ),
    (
        "07",
        "21",
        "단일 원료 건조분말은 0712. 식이보충제·캡슐·복합 분말 믹스는 2106.",
        "제21류 주 + 0712 호 용어",
    ),
    (
        "11",
        "07",
        "곡물·건조채두·견과 원료이고 가루·거친가루·펠릿·전분 형태면 1106. 채소 원료는 절대 11류 아님 (0712).",
        "제11류 주 1(가)",
    ),
]


# -------- 적재 로직 --------


def _sync_engine():
    url = settings.database_url.replace("+asyncpg", "+psycopg")
    return create_engine(url, future=True)


def _merge_unique(existing: list[str] | None, additions: list[str]) -> list[str]:
    """순서 보존 + 중복 제거로 병합."""
    seen: set[str] = set()
    out: list[str] = []
    for item in (existing or []) + additions:
        s = item.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def curate_chapter(session: Session, code: str, data: dict, *, dry_run: bool) -> dict:
    """chapter hs_nodes 한 건 수동 교정."""
    node = session.execute(
        select(HSNode).where(HSNode.version == VERSION, HSNode.code == code, HSNode.level == 2)
    ).scalar_one_or_none()
    if node is None:
        logger.warning("chapter %s hs_node 없음 — skip", code)
        return {"code": code, "status": "missing"}

    before = {
        "incl": len(node.inclusion_keywords or []),
        "excl": len(node.exclusion_keywords or []),
        "essential": node.essential_character,
    }

    node.inclusion_keywords = _merge_unique(node.inclusion_keywords, data.get("inclusion_keywords") or [])
    node.exclusion_keywords = _merge_unique(node.exclusion_keywords, data.get("exclusion_keywords") or [])
    if data.get("essential_character"):
        node.essential_character = data["essential_character"]
    if data.get("notes_append") and data["notes_append"] not in node.notes_excerpt:
        node.notes_excerpt = node.notes_excerpt + data["notes_append"]

    after = {
        "incl": len(node.inclusion_keywords),
        "excl": len(node.exclusion_keywords),
        "essential": node.essential_character,
    }
    logger.info("chapter %s: incl %d→%d, excl %d→%d, essential=%s",
                code, before["incl"], after["incl"],
                before["excl"], after["excl"], after["essential"])
    return {"code": code, "status": "updated", "before": before, "after": after}


def curate_discriminators(session: Session, *, dry_run: bool) -> int:
    """hs_sibling_discriminators 적재 (멱등)."""
    created = 0
    for node_code, vs_code, discriminator, source_ref in MANUAL_DISCRIMINATORS:
        node = session.execute(
            select(HSNode).where(HSNode.version == VERSION, HSNode.code == node_code)
        ).scalar_one_or_none()
        if node is None:
            logger.warning("node %s 없음 — discriminator skip", node_code)
            continue

        # 이미 같은 (node_id, vs_code, discriminator) 가 있으면 skip
        existing = session.execute(
            select(HSSiblingDiscriminator).where(
                HSSiblingDiscriminator.node_id == node.id,
                HSSiblingDiscriminator.vs_code == vs_code,
                HSSiblingDiscriminator.discriminator == discriminator,
            )
        ).scalar_one_or_none()
        if existing:
            continue

        session.add(HSSiblingDiscriminator(
            id=uuid.uuid4(),
            node_id=node.id,
            vs_code=vs_code,
            discriminator=discriminator,
            source_ref=source_ref,
        ))
        created += 1
    logger.info("discriminators created: %d", created)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprint B — 관세사 수동 교정 (Food Solution 류)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = _sync_engine()
    stats: dict = {"chapters": [], "discriminators": 0}
    with Session(engine) as session:
        for code, data in MANUAL_CURATION.items():
            stats["chapters"].append(curate_chapter(session, code, data, dry_run=args.dry_run))
        stats["discriminators"] = curate_discriminators(session, dry_run=args.dry_run)

        if args.dry_run:
            session.rollback()
            logger.info("dry-run — rolled back")
        else:
            session.commit()

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
