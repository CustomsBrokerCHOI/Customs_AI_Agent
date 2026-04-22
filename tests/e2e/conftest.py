"""E2E Playwright 테스트 공용 설정.

## 전제 조건 (로컬 실행)

1. Postgres+pgvector 기동 + migration::

    docker run -d --name customs-pg \
      -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=customs \
      -p 5432:5432 pgvector/pgvector:pg16
    alembic -c api/alembic.ini upgrade head

2. 백엔드::

    uvicorn api.main:app --reload --port 8000
    # ANTHROPIC_API_KEY/OPENAI_API_KEY 가 없으면 mock 엔진이 동작 → E2E 에 충분

3. 프런트엔드::

    cd web && npm install && npm run dev

4. 실행::

    export E2E_API_URL=http://localhost:8000
    export E2E_WEB_URL=http://localhost:3000
    python -m pytest tests/e2e/ -m e2e -v

## Skip 조건
- ``SKIP_E2E=1`` 환경변수
- Playwright/Chromium 미설치
- API URL / Web URL 도달 불가

## 설계
- 각 테스트는 고유 이메일로 사용자 등록 (``uuid.hex``) → 격리
- Browser context 는 테스트 단위 (새 cookie, 새 세션)
- mock 엔진 기준 ~2 초 + 폴링 지연 → 폴링 wait 최대 20 초
"""

from __future__ import annotations

import os
import socket
import ssl
import uuid
from urllib.parse import urlparse

import httpx
import pytest

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_WEB_URL = "http://localhost:3000"


def _probe_playwright() -> str | None:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as exc:
        return f"playwright 미설치: {exc}"
    try:
        from playwright.sync_api import sync_playwright as _sp

        with _sp() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return f"Chromium 런치 실패 (playwright install chromium 필요?): {exc}"
    return None


def _probe_http(url: str, path: str = "/", timeout: float = 5.0) -> str | None:
    """URL 에 HTTP GET. 2xx/3xx/4xx 응답이 오면 도달 가능 (5xx 는 서버 문제로 간주)."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{url.rstrip('/')}{path}", follow_redirects=False)
            if r.status_code >= 500:
                return f"{url}{path} HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"{url}{path} 도달 실패: {exc}"
    return None


def _probe_tcp(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return f"잘못된 URL: {url}"
    try:
        with socket.create_connection((host, port), timeout=5.0):
            pass
    except OSError as exc:
        return f"{host}:{port} 연결 실패: {exc}"
    return None


@pytest.fixture(scope="session")
def api_url() -> str:
    return os.environ.get("E2E_API_URL", DEFAULT_API_URL)


@pytest.fixture(scope="session")
def web_url() -> str:
    return os.environ.get("E2E_WEB_URL", DEFAULT_WEB_URL)


@pytest.fixture(scope="session", autouse=True)
def _e2e_preflight(api_url: str, web_url: str) -> None:
    if os.environ.get("SKIP_E2E", "").strip() in ("1", "true", "yes"):
        pytest.skip("SKIP_E2E 환경변수로 제외됨", allow_module_level=True)

    for probe_err in (
        _probe_playwright(),
        _probe_tcp(api_url),
        _probe_http(api_url, "/health"),
        _probe_tcp(web_url),
        _probe_http(web_url, "/"),
    ):
        if probe_err:
            pytest.skip(probe_err, allow_module_level=True)


@pytest.fixture
def test_credentials() -> dict[str, str]:
    """격리된 테스트 사용자 자격증명 — 매 테스트마다 새 이메일."""
    suffix = uuid.uuid4().hex[:10]
    return {
        "email": f"e2e-{suffix}@example.com",
        "name": "E2E 테스터",
        "password": "E2e-Test-Password-2026!",  # 강도 정책 통과 (대·소·숫·특 + 길이)
    }


@pytest.fixture
def registered_user(api_url: str, test_credentials: dict[str, str]) -> dict[str, str]:
    """API 에 사용자 등록 후 자격증명 반환. 이후 UI 로그인에 재사용."""
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{api_url}/auth/register",
            json={
                "email": test_credentials["email"],
                "name": test_credentials["name"],
                "password": test_credentials["password"],
            },
        )
        if r.status_code not in (200, 201):
            pytest.skip(f"사용자 등록 실패 ({r.status_code}): {r.text[:200]}")
    return test_credentials


@pytest.fixture
def browser_context(web_url: str):
    """테스트 단위 chromium context. 각 테스트는 빈 cookie 상태에서 시작."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            base_url=web_url,
            locale="ko-KR",
            viewport={"width": 1280, "height": 800},
        )
        try:
            yield context
        finally:
            context.close()
            browser.close()


@pytest.fixture
def page(browser_context):
    page = browser_context.new_page()
    page.set_default_timeout(15_000)
    try:
        yield page
    finally:
        page.close()
