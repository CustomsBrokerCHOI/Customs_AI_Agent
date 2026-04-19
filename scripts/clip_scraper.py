"""CLIP(관세법령정보포털) 해설서 스크래퍼 (Playwright 기반).

진입점: ``/clip/hsinfosrch/openULS0202001Q.do``

실측된 DOM 구조:
- 검색 폼 ``#searchVo``: ``#srchManlUtSgn`` (HS 4자리), ``#lworBsopAplyStrtYy`` (연도),
  라디오 ``#iWco`` (WCO/AHTN 등 구분). submit 후 ``#ULS0202001Q_T1_table1`` 에
  결과 행이 생성되며 ``a.dtlInfo`` 클릭 시 상세(``#dtlLayer``) 가 펼쳐진다.
- 상세 탭은 페이지 렌더 시점에 이미 DOM 에 함께 로드되어 있어, 탭 전환 없이
  한 번에 4개(통칙/부/류/호) × 2(국문/영문) 를 수집할 수 있다.

사용 전 설치::

    pip install playwright
    playwright install chromium
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

# 실측된 DOM 셀렉터. CLIP 개편 시 갱신 필요.
SEL_HS_INPUT = "#srchManlUtSgn"
SEL_YEAR_SELECT = "#lworBsopAplyStrtYy"
SEL_WCO_RADIO = "#iWco"
SEL_FORM_SUBMIT = "#searchVo button[type='submit']"
SEL_RESULT_TABLE = "#ULS0202001Q_T1_table1"
SEL_RESULT_DETAIL_LINK = "#ULS0202001Q_T1_table1 a.dtlInfo"
SEL_DETAIL_LAYER = "#dtlLayer"

TAB_SELECTORS: dict[str, tuple[str, str]] = {
    # 탭 id → (국문 pre 셀렉터, 영문 pre 셀렉터)
    "general_rule": ("#divLft_tab1 pre", "#divRght_tab1 pre"),
    "section_note": ("#divLft_tab2 pre", "#divRght_tab2 pre"),
    "chapter_note": ("#divLft_tab3 pre", "#divRght_tab3 pre"),
    "heading_note": ("#divLft_tab4 pre", "#divRght_tab4 pre"),
}


class ClipScrapeError(RuntimeError):
    """CLIP 스크래핑 실패."""


@dataclass
class BilingualText:
    ko: str | None = None
    en: str | None = None

    def is_empty(self) -> bool:
        return not (self.ko or self.en)


@dataclass
class ExplanatoryNote:
    """CLIP HS해설서 한 건. 호(Heading) 단위."""

    heading: str
    year: str
    general_rule: BilingualText = field(default_factory=BilingualText)
    section_note: BilingualText = field(default_factory=BilingualText)
    chapter_note: BilingualText = field(default_factory=BilingualText)
    heading_note: BilingualText = field(default_factory=BilingualText)
    metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "heading": self.heading,
            "year": self.year,
            "general_rule": vars(self.general_rule),
            "section_note": vars(self.section_note),
            "chapter_note": vars(self.chapter_note),
            "heading_note": vars(self.heading_note),
            "metadata": dict(self.metadata),
        }


class ClipScraper(AbstractContextManager["ClipScraper"]):
    """CLIP 해설서 스크래퍼 (Playwright 기반).

    컨텍스트 매니저로 사용한다::

        with ClipScraper(headless=True) as scraper:
            note = scraper.fetch_explanatory_note("8471", year="2022")
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC,
        headless: bool = True,
        storage_state: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.rate_limit_sec = rate_limit_sec
        self.headless = headless
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

    def fetch_explanatory_note(
        self,
        heading_no: str,
        year: str = "2022",
    ) -> ExplanatoryNote:
        """4자리 호(Heading) 번호 기준 해설서 수집.

        :param heading_no: 4자리 호 (예: ``"8471"``).
        :param year: HSK 연도 (``2007/2012/2017/2022``).
        """
        if len(heading_no) != 4 or not heading_no.isdigit():
            raise ValueError(f"heading_no 는 4자리 숫자여야 합니다: {heading_no!r}")

        page = self._require_page()
        self._respect_rate_limit()

        page.goto(f"{self.base_url}{HS_MANUAL_PATH}", wait_until="domcontentloaded")
        self._submit_search(heading_no=heading_no, year=year)
        self._open_detail()

        note = ExplanatoryNote(
            heading=heading_no,
            year=year,
            metadata={
                "source": "CLIP",
                "url": page.url,
                "manl_orgn": "WCO",
            },
        )
        for attr, (ko_sel, en_sel) in TAB_SELECTORS.items():
            setattr(
                note,
                attr,
                BilingualText(
                    ko=self._text_or_none(ko_sel),
                    en=self._text_or_none(en_sel),
                ),
            )

        self._last_request_at = time.monotonic()
        return note

    def fetch_many(
        self,
        heading_nos: Iterable[str],
        year: str = "2022",
    ) -> list[ExplanatoryNote]:
        return [self.fetch_explanatory_note(h, year=year) for h in heading_nos]

    def _submit_search(self, heading_no: str, year: str) -> None:
        page = self._require_page()
        try:
            page.wait_for_selector(SEL_HS_INPUT)
            page.check(SEL_WCO_RADIO)
            page.select_option(SEL_YEAR_SELECT, value=year)
            page.fill(SEL_HS_INPUT, heading_no)
            page.click(SEL_FORM_SUBMIT)
            page.wait_for_selector(f"{SEL_RESULT_TABLE} tbody tr")
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError(
                f"검색 폼 제출/결과 대기 실패 (heading={heading_no}, year={year})"
            ) from exc

    def _open_detail(self) -> None:
        page = self._require_page()
        try:
            page.click(SEL_RESULT_DETAIL_LINK)
            page.wait_for_selector(SEL_DETAIL_LAYER)
            # 탭 본문이 렌더되었는지 최소 한 영역으로 확인
            page.wait_for_selector(TAB_SELECTORS["heading_note"][0])
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError("상세(dtlLayer) 렌더 대기 실패") from exc

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
