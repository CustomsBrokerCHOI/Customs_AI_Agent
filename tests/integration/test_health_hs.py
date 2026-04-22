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
    # FastAPI Path pattern=r"^\d{10}$" 이므로 패턴 위반은 422
    r = client.get("/hs/ABCDEFGHIJ")
    assert r.status_code == 422


def test_hs_lookup_rejects_wrong_length(authed_client) -> None:
    client, _db, _user = authed_client
    r = client.get("/hs/12345")
    assert r.status_code == 422


def test_hs_lookup_returns_404_when_missing(authed_client) -> None:
    client, db, _user = authed_client
    # execute → scalar_one_or_none=None
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)
    r = client.get("/hs/8471300000")
    assert r.status_code == 404
    assert r.json()["detail"] == "HS 부호 없음"


def test_hs_lookup_returns_detail_with_tariffs(authed_client) -> None:
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
    # tariff_rates 관계 — selectinload 로 로딩되므로 그냥 리스트 채움
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
    assert body["hs_code"] == "8471300000"
    assert body["heading"] == "8471"
    assert body["name_kr"] == "휴대용 자동자료처리기계"
    assert len(body["tariff_rates"]) == 1
    assert body["tariff_rates"][0]["fta_code"] == "A"
    assert body["tariff_rates"][0]["tax_rate"] == 8.0
