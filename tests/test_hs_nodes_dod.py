"""Sprint A DoD 검증 — hs_nodes 적재 완료성 회귀 테스트.

5인 회의 합의 DoD:
- 21부 + 96류(77 유보) = 117 레코드 전수 적재.
- 필수 5필드 (code, level, title_ko, parent_code, notes_excerpt) 100% non-null.
  · section(level 0) 은 parent_code NULL 허용.
- parent_code 참조 무결성 — chapter.parent_code ∈ {section.code}.
- notes_excerpt ≥ 50자.
- inclusion/exclusion_keywords 는 자동 파싱이라 빈 배열 허용, 그러나 **전체 레코드 중 50% 이상**
  한쪽이라도 비어있지 않아야 파서가 제 역할을 하고 있다고 본다.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.core.config import settings
from api.db.models import HSNode

VERSION = "HSK-2022"


@pytest.fixture(scope="module")
def sync_session():
    url = settings.database_url.replace("+asyncpg", "+psycopg")
    engine = create_engine(url, future=True)
    with Session(engine) as s:
        yield s


def _load_all(session: Session) -> list[HSNode]:
    return list(
        session.execute(
            select(HSNode).where(HSNode.version == VERSION).order_by(HSNode.level, HSNode.code)
        )
        .scalars()
        .all()
    )


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_sections_count(sync_session: Session) -> None:
    sections = [n for n in _load_all(sync_session) if n.level == 0]
    assert len(sections) == 21, f"section 레코드 수 {len(sections)} ≠ 21"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_chapters_count(sync_session: Session) -> None:
    chapters = [n for n in _load_all(sync_session) if n.level == 2]
    # 77 유보는 제외 → 96.
    assert len(chapters) == 96, f"chapter 레코드 수 {len(chapters)} ≠ 96"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_required_fields_non_null(sync_session: Session) -> None:
    """필수 5필드 non-null. section 의 parent_code 만 예외."""
    for n in _load_all(sync_session):
        assert n.code, f"code 비어있음 id={n.id}"
        assert n.level in (0, 2, 4, 6, 10), f"level={n.level} 비정상 code={n.code}"
        assert n.title_ko, f"title_ko 비어있음 code={n.code}"
        assert n.notes_excerpt, f"notes_excerpt 비어있음 code={n.code}"
        if n.level != 0:
            assert n.parent_code, f"parent_code 비어있음 code={n.code}"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_parent_code_referential_integrity(sync_session: Session) -> None:
    """chapter.parent_code 가 실제 존재하는 section.code 인가."""
    nodes = _load_all(sync_session)
    section_codes = {n.code for n in nodes if n.level == 0}
    for n in nodes:
        if n.level == 2:
            assert n.parent_code in section_codes, (
                f"chapter {n.code} 의 parent_code={n.parent_code!r} 가 section 목록에 없음"
            )


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_notes_excerpt_min_length(sync_session: Session) -> None:
    """notes_excerpt 길이 검증.

    - 류(level 2): ≥ 50자 강제. 류주 원문이 이보다 짧으면 스크래퍼 버그.
    - 부(level 0): WCO HS 기준 일부 부(예: V 광물성 생산품) 는 section note 자체가 거의
      없으므로 non-empty 만 요구. 50자 미만은 정상일 수 있음.
    """
    for n in _load_all(sync_session):
        if n.level == 2:
            assert len(n.notes_excerpt) >= 50, (
                f"류 notes_excerpt 길이 {len(n.notes_excerpt)} < 50 — code={n.code}"
            )
        else:
            assert len(n.notes_excerpt) >= 1, (
                f"부 notes_excerpt 빈 문자열 — code={n.code}"
            )


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_parser_produces_keywords_for_majority(sync_session: Session) -> None:
    """자동 파서가 **최소 50% 레코드** 에서 inclusion 또는 exclusion keyword 1개 이상 뽑아야
    파서가 실제 동작 중임을 보증. 아니면 정규식 조정 필요 신호.
    """
    nodes = _load_all(sync_session)
    with_kw = sum(
        1
        for n in nodes
        if (n.inclusion_keywords and len(n.inclusion_keywords) > 0)
        or (n.exclusion_keywords and len(n.exclusion_keywords) > 0)
    )
    ratio = with_kw / max(1, len(nodes))
    assert ratio >= 0.5, f"keyword 추출 비율 {ratio:.1%} < 50% — 파서 정규식 점검 필요"


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL") and "localhost" not in settings.database_url,
    reason="로컬 DB 필요",
)
def test_unique_version_code(sync_session: Session) -> None:
    """version+code 유일성. 중복 적재 방지 확인."""
    nodes = _load_all(sync_session)
    keys = [(n.version, n.code) for n in nodes]
    assert len(keys) == len(set(keys)), "(version, code) 중복 발견"
