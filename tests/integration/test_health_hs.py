"""health + hs 엔드포인트 통합 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from api.db.models import HSCode, TariffRate

# ---- /health ----


def test_health_returns_ok(authed_client) -> None:
    # health 는 인증 불필요하지만 authed_client 로 재사용 (dep override 만 필요)
    client, db, _user = authed_client
    # execute(text("SELECT 1")) 통과
    db.execute = AsyncMock(return_value=MagicMock())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_health_reports_db_down_on_exception(authed_client) -> None:
    client, db, _user = authed_client
    db.execute = AsyncMock(side_effect=RuntimeError("conn refused"))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db"] == "down"


# ---- /hs/{code} ----


def test_hs_lookup_requires_authentication(unauthed_client) -> None:
    client, _db = unauthed_client
    r = client.get("/hs/8471300000")
    assert r.status_code == 401


def test_hs_lookup_rejects_non_numeric(authed_client) -> None:
    client, _db, _user = authed_client
    r = client.get("/hs/ABCDEFGHIJ")
    assert r.status_code == 422


def test_hs_lookup_rejects_wrong_length(authed_client) -> None:
    client, _db, _user = authed_client
    # 5 자리 — 2/4/6/10 외 길이는 패턴 위반
    r = client.get("/hs/12345")
    assert r.status_code == 422
    # 1 자리
    assert client.get("/hs/1").status_code == 422
    # 8 자리
    assert client.get("/hs/12345678").status_code == 422


def test_hs_lookup_10_digits_returns_404_when_missing(authed_client) -> None:
    client, db, _user = authed_client
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)
    r = client.get("/hs/8471300000")
    assert r.status_code == 404
    assert r.json()["detail"] == "HS 부호 없음"


def test_hs_lookup_10_digits_returns_detail_with_tariffs(authed_client) -> None:
    client, db, _user = authed_client
    hs = HSCode(
        hs_code="8471300000",
        heading="8471",
        sub_heading="30",
        tariff_line="0000",
        name_kr="휴대용 자동자료처리기계",
        name_en="Portable ADP",
        qty_unit="EA",
        weight_unit="KG",
        source="UNIPASS",
    )
    tariff = TariffRate(
        hs_code="8471300000",
        fta_code="A",
        fta_name="기본세율",
        tax_rate=8.0,
        per_unit_tax=None,
        base_price=None,
        apply_start=None,
        apply_end=None,
        source="UNIPASS",
    )
    hs.tariff_rates = [tariff]  # type: ignore[assignment]

    res = MagicMock()
    res.scalar_one_or_none.return_value = hs
    db.execute = AsyncMock(return_value=res)

    r = client.get("/hs/8471300000")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "tariff_line"
    assert body["code"] == "8471300000"
    assert body["chapter_number"] == 84
    # 제84류 → 제XVI부
    assert body["section"]["roman"] == "XVI"

    detail = body["detail"]
    assert detail["hs_code"] == "8471300000"
    assert detail["heading"] == "8471"
    assert detail["name_kr"] == "휴대용 자동자료처리기계"
    assert len(detail["tariff_rates"]) == 1
    assert detail["tariff_rates"][0]["fta_code"] == "A"
    assert detail["tariff_rates"][0]["tax_rate"] == 8.0

    assert body["children"] == []


# ---- 2/4/6 자리 집계 ----


def _rows(*pairs: tuple[str, str | None, str | None]) -> MagicMock:
    """``(await db.execute(stmt)).all()`` 가 돌려줄 Row 유사 객체 리스트."""
    rows = []
    for code, name_kr, name_en in pairs:
        r = MagicMock()
        r.code = code
        r.name_kr = name_kr
        r.name_en = name_en
        rows.append(r)
    res = MagicMock()
    res.all.return_value = rows
    return res


def test_hs_lookup_2_digits_returns_headings(authed_client) -> None:
    client, db, _user = authed_client
    db.execute = AsyncMock(
        return_value=_rows(
            ("8471", "휴대용 자동자료처리기계", "Portable ADP"),
            ("8517", "전화기", "Telephones"),
        )
    )
    r = client.get("/hs/84")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "chapter"
    assert body["code"] == "84"
    assert body["chapter_number"] == 84
    assert body["section"]["roman"] == "XVI"
    assert body["detail"] is None
    assert [c["code"] for c in body["children"]] == ["8471", "8517"]


def test_hs_lookup_4_digits_returns_subheadings(authed_client) -> None:
    client, db, _user = authed_client
    db.execute = AsyncMock(
        return_value=_rows(
            ("847130", "휴대용 ADP", "Portable ADP"),
            ("847141", "데스크톱 ADP", "Desktop ADP"),
        )
    )
    r = client.get("/hs/8471")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "heading"
    assert body["code"] == "8471"
    assert body["chapter_number"] == 84
    assert [c["code"] for c in body["children"]] == ["847130", "847141"]


def test_hs_lookup_6_digits_returns_tariff_lines(authed_client) -> None:
    client, db, _user = authed_client
    db.execute = AsyncMock(
        return_value=_rows(
            ("8471300000", "휴대용 자동자료처리기계", "Portable ADP"),
        )
    )
    r = client.get("/hs/847130")
    assert r.status_code == 200
    body = r.json()
    assert body["level"] == "subheading"
    assert body["code"] == "847130"
    assert body["chapter_number"] == 84
    assert len(body["children"]) == 1
    assert body["children"][0]["code"] == "8471300000"


def test_hs_lookup_2_digits_returns_404_when_empty(authed_client) -> None:
    client, db, _user = authed_client
    db.execute = AsyncMock(return_value=_rows())  # 빈 결과
    r = client.get("/hs/99")
    assert r.status_code == 404
    assert r.json()["detail"] == "해당 범위에 HS 부호가 없습니다."
