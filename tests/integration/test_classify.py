"""classify 엔드포인트 통합 테스트 (라우팅·권한·상태코드·스키마)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from api.db.models import ClassifyJob

# ---- 공용 헬퍼 ----


def _make_job(user_id: uuid.UUID, **overrides) -> ClassifyJob:
    job = ClassifyJob(
        user_id=user_id,
        product_name=overrides.get("product_name", "노트북"),
        description=overrides.get("description", "휴대용 컴퓨터"),
        image_url=None,
        status=overrides.get("status", "complete"),
        result=overrides.get(
            "result",
            {
                "candidates": [
                    {
                        "rank": 1,
                        "hs_code": "8471300000",
                        "heading": "8471",
                        "sub_heading": "30",
                        "name_kr": "휴대용 자동자료처리기계",
                        "name_en": "Portable ADP",
                        "breadcrumb": ["제XVI부", "제84류", "제8471호"],
                        "confidence": 0.9,
                        "base_tariff_rate": None,
                        "verified": True,
                        "verdict": "match",
                        "citations": [],
                    }
                ],
                "notice": "Draft.",
                "meta": {"engine": "stub", "draft": True},
            },
        ),
        error_message=None,
        reviewed=overrides.get("reviewed", False),
        accepted_hs_code=overrides.get("accepted_hs_code"),
        hsk_year=2022,
    )
    job.id = overrides.get("id", uuid.uuid4())
    job.created_at = overrides.get("created_at", datetime.now(timezone.utc))
    job.completed_at = overrides.get("completed_at", datetime.now(timezone.utc))
    return job


def _rate_limit_ok(db, count: int = 0):
    """rate_limit 서비스의 count 쿼리 스텁 — count 반환."""
    res = MagicMock()
    res.scalar_one.return_value = count
    db.execute.return_value = res  # type: ignore[attr-defined]


# ---- POST /classify ----


def test_create_classify_requires_auth(unauthed_client) -> None:
    client, _db = unauthed_client
    r = client.post("/classify", json={"product_name": "x", "description": "y"})
    assert r.status_code == 401


def test_create_classify_rejects_short_product_name(authed_client) -> None:
    client, _db, _u = authed_client
    r = client.post("/classify", json={"product_name": "", "description": "y"})
    assert r.status_code == 422


def test_create_classify_returns_202_and_records_audit(authed_client, monkeypatch) -> None:
    client, db, user = authed_client
    _rate_limit_ok(db, count=5)  # 한도 미달

    # flush 시점에 실 DB 가 server_default 로 채워주는 값들을 흉내
    added: list = []
    db.add.side_effect = lambda obj: added.append(obj)

    async def _populate_defaults() -> None:
        for obj in added:
            if isinstance(obj, ClassifyJob) and obj.id is None:
                obj.id = uuid.uuid4()
                obj.created_at = datetime.now(timezone.utc)

    db.flush.side_effect = _populate_defaults

    # 백그라운드 잡이 실 Postgres 로 연결 시도하지 않도록 무력화
    async def _noop(job_id):  # type: ignore[unused-argument]
        return None

    monkeypatch.setattr("api.routers.classify._run_classify_job", _noop)

    r = client.post(
        "/classify",
        json={"product_name": "노트북", "description": "휴대용 컴퓨터"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "pending"

    # ClassifyJob + AuditLog 두 행 add
    added_types = {type(o).__name__ for o in added}
    assert "ClassifyJob" in added_types
    assert "AuditLog" in added_types
    db.commit.assert_awaited()


def test_create_classify_429_when_over_quota(authed_client) -> None:
    client, db, _u = authed_client
    # settings.rate_limit_per_day=100 기본 → 이미 100 건
    _rate_limit_ok(db, count=100)

    r = client.post(
        "/classify",
        json={"product_name": "노트북", "description": "x"},
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 0
    detail = r.json()["detail"]
    assert "100" in detail  # used_today


# ---- GET /classify/{id} ----


def test_get_classify_job_owner_returns_200(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id)
    db.get = AsyncMock(return_value=job)
    r = client.get(f"/classify/{job.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(job.id)
    assert body["product_name"] == "노트북"
    assert body["result"]["candidates"][0]["hs_code"] == "8471300000"


def test_get_classify_job_404_for_missing(authed_client) -> None:
    client, db, _user = authed_client
    db.get = AsyncMock(return_value=None)
    r = client.get(f"/classify/{uuid.uuid4()}")
    assert r.status_code == 404


def test_get_classify_job_404_for_other_owner(authed_client) -> None:
    client, db, user = authed_client
    other_owner = uuid.uuid4()
    job = _make_job(other_owner)
    db.get = AsyncMock(return_value=job)
    r = client.get(f"/classify/{job.id}")
    # 존재 유출 방지: 404 로 통일
    assert r.status_code == 404


# ---- GET /classify (list) ----


def test_list_classify_jobs_returns_user_rows(authed_client) -> None:
    client, db, user = authed_client
    j1 = _make_job(user.id, product_name="A")
    j2 = _make_job(user.id, product_name="B")
    res = MagicMock()
    res.scalars.return_value = MagicMock(all=MagicMock(return_value=[j1, j2]))
    db.execute = AsyncMock(return_value=res)

    r = client.get("/classify?limit=10&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert {row["product_name"] for row in body} == {"A", "B"}
    # 상세 result 필드는 JobSummary 에서 제외
    assert "result" not in body[0]
    assert "description" not in body[0]


def test_list_classify_jobs_requires_auth(unauthed_client) -> None:
    client, _db = unauthed_client
    r = client.get("/classify")
    assert r.status_code == 401


# ---- POST /classify/{id}/review ----


def test_review_marks_reviewed_and_sets_accepted(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="complete")
    db.get = AsyncMock(return_value=job)

    r = client.post(
        f"/classify/{job.id}/review",
        json={"accepted_hs_code": "8471300000"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reviewed"] is True
    assert body["accepted_hs_code"] == "8471300000"
    # AuditLog 기록
    added_types = {type(call.args[0]).__name__ for call in db.add.call_args_list}
    assert "AuditLog" in added_types


def test_review_rejected_clears_accepted(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="complete", accepted_hs_code="8471300000")
    db.get = AsyncMock(return_value=job)

    r = client.post(
        f"/classify/{job.id}/review",
        json={"rejected": True},
    )
    assert r.status_code == 200
    assert r.json()["accepted_hs_code"] is None


def test_review_409_when_not_complete(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="processing")
    db.get = AsyncMock(return_value=job)

    r = client.post(
        f"/classify/{job.id}/review",
        json={"accepted_hs_code": "8471300000"},
    )
    assert r.status_code == 409
    assert "processing" in r.json()["detail"]


def test_review_404_for_other_owner(authed_client) -> None:
    client, db, _user = authed_client
    job = _make_job(uuid.uuid4(), status="complete")
    db.get = AsyncMock(return_value=job)

    r = client.post(f"/classify/{job.id}/review", json={"rejected": True})
    assert r.status_code == 404


def test_review_422_for_wrong_length_hs(authed_client) -> None:
    client, _db, _user = authed_client
    r = client.post(
        f"/classify/{uuid.uuid4()}/review",
        json={"accepted_hs_code": "1234"},  # 10자리 필요
    )
    assert r.status_code == 422


# ---- GET /classify/{id}/report.html ----


def test_report_html_owner_complete_returns_html(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="complete")
    db.get = AsyncMock(return_value=job)
    # run_sync: 실제 DB 없으므로 sync 콜러블을 None session 으로 호출한 결과만 돌려줌.
    db.run_sync = AsyncMock(side_effect=lambda fn: fn(None))

    r = client.get(f"/classify/{job.id}/report.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "품목분류의견서" in r.text
    assert "노트북" in r.text
    assert "Draft" in r.text


def test_report_html_409_when_not_complete(authed_client) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="processing")
    db.get = AsyncMock(return_value=job)

    r = client.get(f"/classify/{job.id}/report.html")
    assert r.status_code == 409


def test_report_html_404_for_other_owner(authed_client) -> None:
    client, db, _user = authed_client
    job = _make_job(uuid.uuid4(), status="complete")
    db.get = AsyncMock(return_value=job)

    r = client.get(f"/classify/{job.id}/report.html")
    assert r.status_code == 404


# ---- GET /classify/{id}/report.pdf ----


def test_report_pdf_501_when_weasyprint_missing(authed_client, monkeypatch) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="complete")
    db.get = AsyncMock(return_value=job)
    db.run_sync = AsyncMock(side_effect=lambda fn: fn(None))

    # weasyprint import 를 강제로 실패시킴
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise ImportError("mock missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    r = client.get(f"/classify/{job.id}/report.pdf")
    assert r.status_code == 501
    assert "weasyprint" in r.json()["detail"]


def test_report_pdf_200_when_weasyprint_mocked(authed_client, monkeypatch) -> None:
    client, db, user = authed_client
    job = _make_job(user.id, status="complete")
    db.get = AsyncMock(return_value=job)
    db.run_sync = AsyncMock(side_effect=lambda fn: fn(None))

    import sys
    import types as pytypes

    fake_module = pytypes.ModuleType("weasyprint")

    class _FakeHTML:
        def __init__(self, *a, **kw):
            pass

        def write_pdf(self) -> bytes:
            return b"%PDF-1.4 test"

    fake_module.HTML = _FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", fake_module)

    r = client.get(f"/classify/{job.id}/report.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.content.startswith(b"%PDF")
