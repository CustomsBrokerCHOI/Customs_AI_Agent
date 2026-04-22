"""scripts.build_index 단위 테스트 — cases 서브커맨드 위주 (DB/네트워크 없음).

Session 은 ``MagicMock`` 으로 대체하고, ``execute`` 로 전달되는 SQL stmt 를
PostgreSQL dialect 로 컴파일해 파라미터·ON CONFLICT 여부를 검증한다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql.dml import Insert as PGInsert

from scripts.build_index import (
    _normalize_hs,
    _parse_decision_date,
    upsert_cases_from_jsonl,
)


# ---- _parse_decision_date ----


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2024-01-15", date(2024, 1, 15)),
        ("2024.01.15", date(2024, 1, 15)),
        ("2024/01/15", date(2024, 1, 15)),
        ("20240115", date(2024, 1, 15)),
    ],
)
def test_parse_decision_date_handles_multiple_formats(raw: str, expected: date) -> None:
    assert _parse_decision_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "not-a-date", "2024/13/40"])
def test_parse_decision_date_returns_none_for_invalid(raw: str | None) -> None:
    assert _parse_decision_date(raw) is None


# ---- _normalize_hs ----


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8471300000", "8471300000"),
        ("8471.30-0000", "8471300000"),
        ("8471 30 0000", "8471300000"),
        ("8471-30-0000", "8471300000"),
    ],
)
def test_normalize_hs_strips_separators(raw: str, expected: str) -> None:
    assert _normalize_hs(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "847130000", "abcdefghij", "847130000X"])
def test_normalize_hs_rejects_bad_format(raw: str | None) -> None:
    assert _normalize_hs(raw) is None


# ---- upsert_cases_from_jsonl ----


def _make_session(existing_hs: list[str]) -> MagicMock:
    """execute().all() 가 existing_hs 튜플 리스트를 반환하는 MagicMock Session."""
    session = MagicMock()
    session.execute.return_value.all.return_value = [(h,) for h in existing_hs]
    return session


def _insert_stmts(session: MagicMock) -> list[PGInsert]:
    """첫 번째 select 호출을 제외한 나머지 execute 호출의 stmt 인자를 수집."""
    stmts: list[PGInsert] = []
    for call in session.execute.call_args_list:
        arg = call.args[0]
        if isinstance(arg, PGInsert):
            stmts.append(arg)
    return stmts


def _compiled_params(stmt: PGInsert) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


def _compiled_sql(stmt: PGInsert) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_upsert_cases_nulls_unknown_hs_fk(tmp_path: Path) -> None:
    """hs_codes 에 없는 HS 는 FK 오류 방지로 NULL 로 강등."""
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "case_ref": "CASE-001",
                "product_name": "미상의 전자제품",
                "hs_code": "9999999999",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = _make_session(existing_hs=["8471300000"])

    inserted, nulled, total = upsert_cases_from_jsonl(session, jsonl)

    assert (inserted, nulled, total) == (1, 1, 1)
    stmts = _insert_stmts(session)
    assert len(stmts) == 1
    params = _compiled_params(stmts[0])
    assert params["hs_code"] is None


def test_upsert_cases_preserves_known_hs(tmp_path: Path) -> None:
    """hs_codes 에 존재하는 HS 는 그대로 적재."""
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "case_ref": "CASE-002",
                "product_name": "노트북",
                "hs_code": "8471.30-0000",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = _make_session(existing_hs=["8471300000"])

    inserted, nulled, total = upsert_cases_from_jsonl(session, jsonl)

    assert (inserted, nulled, total) == (1, 0, 1)
    params = _compiled_params(_insert_stmts(session)[0])
    assert params["hs_code"] == "8471300000"


def test_upsert_cases_skips_empty_product_name(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"case_ref": "A", "product_name": "", "hs_code": None}),
                json.dumps({"case_ref": "B", "product_name": "   ", "hs_code": None}),
                json.dumps({"case_ref": "C", "product_name": "실제 품목", "hs_code": None}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    session = _make_session(existing_hs=[])

    inserted, nulled, total = upsert_cases_from_jsonl(session, jsonl)

    # 빈 라인은 total 에서도 제외. 빈 product_name 2건은 total 에 포함되되 insert 안됨.
    assert total == 3
    assert inserted == 1
    assert nulled == 0


def test_upsert_cases_uses_on_conflict_for_case_ref(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        json.dumps({"case_ref": "REF-1", "product_name": "x"}) + "\n",
        encoding="utf-8",
    )
    session = _make_session(existing_hs=[])

    upsert_cases_from_jsonl(session, jsonl)

    sql = _compiled_sql(_insert_stmts(session)[0])
    assert "ON CONFLICT" in sql
    assert "case_ref" in sql


def test_upsert_cases_plain_insert_without_case_ref(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        json.dumps({"case_ref": None, "product_name": "x"}) + "\n",
        encoding="utf-8",
    )
    session = _make_session(existing_hs=[])

    upsert_cases_from_jsonl(session, jsonl)

    sql = _compiled_sql(_insert_stmts(session)[0])
    assert "ON CONFLICT" not in sql


def test_upsert_cases_parses_decision_date_and_trims_fields(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        json.dumps(
            {
                "case_ref": "D-1",
                "product_name": "노트북  ",
                "hs_code": "8471300000",
                "decision_date": "2024.03.05",
                "description": " 상세 설명 ",
                "reasoning": " 근거 ",
                "source_url": " https://example.com/case/1 ",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = _make_session(existing_hs=["8471300000"])

    upsert_cases_from_jsonl(session, jsonl)

    params = _compiled_params(_insert_stmts(session)[0])
    assert params["decision_date"] == date(2024, 3, 5)
    assert params["description"] == "상세 설명"
    assert params["reasoning"] == "근거"
    assert params["source_url"] == "https://example.com/case/1"


def test_upsert_cases_ignores_malformed_jsonl_line(tmp_path: Path) -> None:
    jsonl = tmp_path / "cases.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                "{ not valid json",
                json.dumps({"case_ref": "OK", "product_name": "x"}),
            ]
        ),
        encoding="utf-8",
    )
    session = _make_session(existing_hs=[])

    inserted, _, total = upsert_cases_from_jsonl(session, jsonl)

    assert total == 2  # 두 라인 다 카운트하되
    assert inserted == 1  # 실제 insert 는 파싱 성공한 1건만
    assert len(_insert_stmts(session)) == 1
