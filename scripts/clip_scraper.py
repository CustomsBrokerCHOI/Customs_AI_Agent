"""CLIP(관세법령정보포털) 해설서·주규정 수집 스크래퍼 (Playwright 기반).

CLIP은 jQuery 1.10 + `jquery.tmpl` 로 **클라이언트 사이드 렌더링**하는 사이트다.
`requests + BeautifulSoup` 으로는 빈 템플릿만 수신되므로, 브라우저를 구동해
JS 실행 후 렌더된 DOM을 수집한다.

사용 전 설치::

    pip install playwright
    playwright install chromium

운영 적용 전 반드시 점검할 것:

1. 대상 페이지(``HS_MANUAL_PATH``)의 실제 네비게이션 플로우 — 호 번호 선택, iframe,
   탭 전환 등이 DOM 구조에 영향을 줄 수 있다.
2. `Selectors` 의 기본값은 렌더된 페이지 점검 후 갱신 필요. 현재 값은
   CLIP의 `leftmenu`, `mainarea` 등 공통 컨테이너 힌트에 기반한 추정치이다.
3. robots.txt · 이용약관 상 자동 수집 허용 여부 확인.
4. SSO/로그인 필요 여부 — 해설서 조회는 통상 공개이나, 일부 기능은
   ``storage_state`` 로 쿠키·세션 주입이 필요할 수 있다.
"""

from __future__ import annotations

import logging
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Iterable

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://unipass.customs.go.kr"
HS_MANUAL_PATH = "/clip/hsinfosrch/openULS0202001Q.do"
DEFAULT_USER_AGENT = "CustomsAIAgent/0.1 (+contact: ops@example.com)"
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_RATE_LIMIT_SEC = 1.0


@dataclass(frozen=True)
class Selectors:
    """렌더된 CLIP 페이지의 DOM 셀렉터. 실제 페이지 점검 후 갱신 필요."""

    ready_indicator: str = "#mainarea :not(:empty)"  # 렌더 완료 판정 힌트
    section_note: str = "[data-kind='section-note'], .section-note"
    chapter_note: str = "[data-kind='chapter-note'], .chapter-note"
    heading_body: str = "[data-kind='heading-body'], .heading-content, #mainarea"
    heading_search_input: str = "#uniSrchText2"
    heading_search_submit: str = "#btnHsSearch"


class ClipScrapeError(RuntimeError):
    """CLIP 스크래핑 실패."""


@dataclass
class ExplanatoryNote:
    heading: str
    section_note: str | None
    chapter_note: str | None
    content: str | None
    metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "heading": self.heading,
            "section_note": self.section_note,
            "chapter_note": self.chapter_note,
            "content": self.content,
            "metadata": dict(self.metadata),
        }


class ClipScraper(AbstractContextManager["ClipScraper"]):
    """Playwright 기반 CLIP 해설서 스크래퍼.

    컨텍스트 매니저로 사용하여 브라우저 리소스를 안전하게 정리한다::

        with ClipScraper(headless=True) as scraper:
            note = scraper.fetch_explanatory_note("8471")
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC,
        hsk_version: str = "2022",
        headless: bool = True,
        selectors: Selectors = Selectors(),
        storage_state: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.rate_limit_sec = rate_limit_sec
        self.hsk_version = hsk_version
        self.headless = headless
        self.selectors = selectors
        self.storage_state = storage_state

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_request_at: float = 0.0

    def __enter__(self) -> "ClipScraper":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            storage_state=self.storage_state,
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        for closer in (self._page, self._context, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # noqa: BLE001
                    logger.exception("Playwright 리소스 해제 중 예외")
        if self._playwright is not None:
            self._playwright.stop()
        self._page = self._context = self._browser = self._playwright = None

    def fetch_explanatory_note(self, heading_no: str) -> ExplanatoryNote:
        """4자리 호 번호 기준 해설서 수집."""
        page = self._require_page()
        self._respect_rate_limit()

        url = f"{self.base_url}{HS_MANUAL_PATH}"
        logger.debug("CLIP goto %s (heading=%s)", url, heading_no)
        page.goto(url, wait_until="domcontentloaded")

        try:
            page.wait_for_selector(self.selectors.ready_indicator)
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError("CLIP 페이지 초기 렌더 대기 실패") from exc

        self._select_heading(heading_no)

        try:
            page.wait_for_load_state("networkidle")
        except PlaywrightTimeoutError:
            logger.warning("networkidle 대기 타임아웃 — 부분 수집 가능성 있음")

        note = ExplanatoryNote(
            heading=heading_no,
            section_note=self._text_or_none(self.selectors.section_note),
            chapter_note=self._text_or_none(self.selectors.chapter_note),
            content=self._text_or_none(self.selectors.heading_body),
            metadata={
                "source": "CLIP",
                "hsk_version": self.hsk_version,
                "url": page.url,
            },
        )
        self._last_request_at = time.monotonic()
        return note

    def fetch_many(self, heading_nos: Iterable[str]) -> list[ExplanatoryNote]:
        return [self.fetch_explanatory_note(h) for h in heading_nos]

    def _select_heading(self, heading_no: str) -> None:
        """상단 세번∙상품검색 입력창에 호 번호 입력 후 검색.

        실제 해설서 탐색 방식은 좌측 트리 클릭이 자연스러울 수 있으므로,
        실페이지 확인 후 이 메서드는 트리 내비게이션으로 교체될 가능성이 높다.
        """
        page = self._require_page()
        try:
            page.fill(self.selectors.heading_search_input, heading_no)
            page.click(self.selectors.heading_search_submit)
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError(f"호 번호 입력/검색 실패: {heading_no}") from exc

    def _text_or_none(self, selector: str) -> str | None:
        page = self._require_page()
        node = page.query_selector(selector)
        if node is None:
            return None
        text = (node.inner_text() or "").strip()
        return text or None

    def _require_page(self) -> Page:
        if self._page is None:
            raise ClipScrapeError(
                "ClipScraper 는 컨텍스트 매니저(`with ...`)로 사용해야 합니다."
            )
        return self._page

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
