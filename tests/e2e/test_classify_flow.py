"""분류 워크플로우 E2E (로그인 → 제출 → 결과 → 확인).

LLM API 키가 세팅되어 있지 않은 경우 백엔드는 ``mock_result`` 로 fallback — 고정
후보(8471300000) + 고정 notice 를 반환한다. 키가 있으면 실엔진이 돌지만 E2E 에서는
mock 기준으로 검증(실엔진은 `tests/test_classify_engine.py` + CI integration-db 에서 커버).
"""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.e2e]


def _login_ui(page, credentials: dict[str, str]) -> None:
    page.goto("/login", wait_until="domcontentloaded")
    page.get_by_label("이메일").fill(credentials["email"])
    page.get_by_label("비밀번호").fill(credentials["password"])
    page.get_by_role("button", name="로그인").click()
    # /login → / 리다이렉트. 대시보드 헤더 대기.
    page.wait_for_url(re.compile(r".*/(?:$|\?)"))
    page.get_by_role("heading", name="대시보드").wait_for(timeout=10_000)


def test_login_redirects_to_dashboard(page, registered_user) -> None:
    _login_ui(page, registered_user)
    # Draft 배너는 layout 에 상시 노출 (Phase 4-B 요구)
    assert "Draft" in page.content() or "초안" in page.content()


def test_full_classify_flow(page, registered_user) -> None:
    """로그인 → /classify/new 입력 → 결과 페이지 → "완료" + 후보 카드 확인."""
    _login_ui(page, registered_user)

    page.goto("/classify/new", wait_until="domcontentloaded")
    page.get_by_role("heading", name="새 분류 요청").wait_for()

    page.get_by_label(re.compile(r"^품명")).fill("휴대용 노트북 컴퓨터 M3 맥북에어")
    page.get_by_label(re.compile(r"^상세 설명")).fill(
        "휴대용 자동자료처리기계. 중량 1.24kg, 13인치 디스플레이, M3 칩, "
        "8GB RAM. 키보드 일체형. 주 용도는 문서작업과 개발."
    )
    page.get_by_role("button", name="분류 요청").click()

    # /classify/{uuid} 로 라우팅
    page.wait_for_url(re.compile(r".*/classify/[0-9a-f-]{36}$"), timeout=10_000)

    # 완료 배지가 나타날 때까지 대기 (mock ~2s + 폴링 2.5s + 여유)
    page.get_by_text("완료", exact=True).wait_for(timeout=20_000)

    # mock 은 8471300000 고정 후보 반환
    assert "후보 HS CODE" in page.content()
    assert "8471" in page.content()

    # Draft 면책은 상시 노출
    body_text = page.locator("body").inner_text()
    assert "초안" in body_text or "Draft" in body_text


def test_invalid_login_shows_error(page, registered_user) -> None:
    page.goto("/login", wait_until="domcontentloaded")
    page.get_by_label("이메일").fill(registered_user["email"])
    page.get_by_label("비밀번호").fill("WrongPassword-9999!")
    page.get_by_role("button", name="로그인").click()

    # 에러 메시지가 로그인 폼 아래 빨간 텍스트로 표시 — 정확한 문구는 보장 안 하고
    # "로그인 실패" 혹은 "401" 류 메시지가 잡히면 통과.
    error_locator = page.locator("p.text-red-600, .text-red-700").first
    error_locator.wait_for(timeout=8_000)
    txt = error_locator.inner_text()
    assert txt.strip(), "에러 메시지가 비어있음"
    # 대시보드로 가지 않았음을 확인 (URL 변경 없음)
    assert "/login" in page.url
