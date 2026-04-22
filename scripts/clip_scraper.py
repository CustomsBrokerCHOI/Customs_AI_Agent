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

import json
import logging
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

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
TARIFF_SCHEDULE_PATH = "/clip/hsinfosrch/openULS0201002Q.do"
CLASSIFICATION_CASE_PATH = "/clip/hsinfosrch/openULS0203042S.do"
FAQ_PATH = "/clip/hsinfosrch/openULS0206017Q.do"
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

SEL_TARIFF_INPUT = "#uniSrchText2"
SEL_TARIFF_SUBMIT = "#btnHsSearch"
SEL_TARIFF_RESULT = "#ULS0201005Q_TBL"

# 품목분류 사례 (openULS0203042S). 실측 전 잠정값이며 dev_probe_cases.py --headed 로
# 실제 DOM 확인 후 확정한다. 검색창은 '품목명/HS부호' 공용 입력으로 관찰되어 단일 셀렉터.
SEL_CASE_INPUT = "#srchText"
SEL_CASE_SUBMIT = "#btnSearch"
SEL_CASE_RESULT = "#ULS0203042S_T1_table1"
SEL_CASE_DETAIL_LINK = "#ULS0203042S_T1_table1 a.dtlInfo"
SEL_CASE_DETAIL_LAYER = "#dtlLayer"
SEL_CASE_PAGINATION_NEXT = "a.paging.next"

# FAQ (openULS0206017Q). 실측 전 잠정값. dev_probe_faq.py --headed 로
# 확인 후 ``_parse_faq_row`` 매핑과 함께 갱신.
SEL_FAQ_INPUT = "#srchText"
SEL_FAQ_SUBMIT = "#btnSearch"
SEL_FAQ_RESULT = "#ULS0206017Q_T1_table1"
SEL_FAQ_DETAIL_LINK = "#ULS0206017Q_T1_table1 a.dtlInfo"
SEL_FAQ_DETAIL_LAYER = "#dtlLayer"
SEL_FAQ_PAGINATION_NEXT = "a.paging.next"

TAB_SELECTORS: dict[str, tuple[str, str]] = {
    # 탭 id → (국문 pre 셀렉터, 영문 pre 셀렉터)
    "general_rule": ("#divLft_tab1 pre", "#divRght_tab1 pre"),
    "section_note": ("#divLft_tab2 pre", "#divRght_tab2 pre"),
    "chapter_note": ("#divLft_tab3 pre", "#divRght_tab3 pre"),
    "heading_note": ("#divLft_tab4 pre", "#divRght_tab4 pre"),
}


class ClipScrapeError(RuntimeError):
    """CLIP 스크래핑 실패."""


def _slug(text: str) -> str:
    """파일 저장용 안전한 slug. ASCII·한글만 남기고 공백→_, 길이 30자 제한."""
    safe = "".join(
        ch if (ch.isalnum() or "\uac00" <= ch <= "\ud7a3") else "_" for ch in text.strip()
    )
    return (safe or "query")[:30]


@dataclass
class BilingualText:
    ko: str | None = None
    en: str | None = None

    def is_empty(self) -> bool:
        return not (self.ko or self.en)


@dataclass
class TariffLine:
    """CLIP 관세율표 한 행 (10자리 세번)."""

    heading: str
    sub_heading: str
    tariff_line: str
    name_kr: str | None
    name_en: str | None
    base_rate: str | None
    flex_rate_cls: str | None  # 탄력세율 구분 (예: C)
    country_rate: str | None  # 한국 표준세율 혹은 비교국 협정세율

    @property
    def hs10(self) -> str:
        return f"{self.heading}{self.sub_heading}{self.tariff_line}".replace(" ", "")


@dataclass
class ClassificationCase:
    """품목분류 사례 한 건 (openULS0203042S)."""

    case_ref: str | None
    product_name: str
    hs_code: str | None = None
    decision_date: str | None = None
    description: str | None = None
    reasoning: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "case_ref": self.case_ref,
            "product_name": self.product_name,
            "hs_code": self.hs_code,
            "decision_date": self.decision_date,
            "description": self.description,
            "reasoning": self.reasoning,
            "source_url": self.source_url,
            "metadata": dict(self.metadata),
        }


@dataclass
class FAQEntry:
    """CLIP FAQ 한 건 (openULS0206017Q).

    .. warning::
        필드 매핑(``question``/``answer``/``category``)은 실제 사이트 DOM
        확인 전 잠정값. ``dev_probe_faq.py --headed`` 로 구조 확정 후 ``_parse_faq_row``
        를 갱신할 것.
    """

    faq_id: str | None
    question: str
    category: str | None = None
    answer: str | None = None
    hs_code: str | None = None
    decision_date: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "faq_id": self.faq_id,
            "question": self.question,
            "category": self.category,
            "answer": self.answer,
            "hs_code": self.hs_code,
            "decision_date": self.decision_date,
            "source_url": self.source_url,
            "metadata": dict(self.metadata),
        }


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
        raw_html_dir: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> None:
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.rate_limit_sec = rate_limit_sec
        self.headless = headless
        self.storage_state = storage_state
        self.raw_html_dir = Path(raw_html_dir) if raw_html_dir else None
        self.manifest_path = Path(manifest_path) if manifest_path else None

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_request_at: float = 0.0

    def __enter__(self) -> ClipScraper:
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

        html_path = self._persist_raw_html(subdir="notes", stem=f"{heading_no}_{year}")

        note = ExplanatoryNote(
            heading=heading_no,
            year=year,
            metadata={
                "source": "CLIP",
                "url": page.url,
                "manl_orgn": "WCO",
            },
        )
        if html_path is not None:
            note.metadata["raw_html_path"] = str(html_path)
        for attr, (ko_sel, en_sel) in TAB_SELECTORS.items():
            setattr(
                note,
                attr,
                BilingualText(
                    ko=self._text_or_none(ko_sel),
                    en=self._text_or_none(en_sel),
                ),
            )

        self._append_manifest(
            {
                "kind": "explanatory_note",
                "heading": heading_no,
                "year": year,
                "url": page.url,
                "raw_html_path": str(html_path) if html_path else None,
            }
        )

        self._last_request_at = time.monotonic()
        return note

    def fetch_many(
        self,
        heading_nos: Iterable[str],
        year: str = "2022",
    ) -> list[ExplanatoryNote]:
        return [self.fetch_explanatory_note(h, year=year) for h in heading_nos]

    def fetch_tariff_schedule(
        self,
        heading_no: str,
        result_timeout_ms: int = 15_000,
    ) -> list[TariffLine]:
        """CLIP 관세율표 (`openULS0201002Q.do`) 에서 HS 4자리 기준 전체 세번 리스트 수집.

        :param heading_no: 4자리 호 번호 (예: ``"8471"``). 10자리도 허용(상위 4자리만 사용).
        :param result_timeout_ms: 검색 결과 테이블 렌더 대기 타임아웃.
        :returns: 10자리 세번 단위 ``TariffLine`` 리스트.
        """
        if len(heading_no) < 4 or not heading_no[:4].isdigit():
            raise ValueError(f"heading_no 의 앞 4자리는 숫자: {heading_no!r}")
        heading_no = heading_no[:4]

        page = self._require_page()
        self._respect_rate_limit()

        page.goto(
            f"{self.base_url}{TARIFF_SCHEDULE_PATH}",
            wait_until="domcontentloaded",
        )
        try:
            page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
            page.fill(SEL_TARIFF_INPUT, heading_no)
            page.click(SEL_TARIFF_SUBMIT)
            page.wait_for_selector(f"{SEL_TARIFF_RESULT} tbody tr", timeout=result_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError(f"관세율표 검색 실패 (heading={heading_no})") from exc

        html_path = self._persist_raw_html(subdir="tariffs", stem=heading_no)

        raw = page.evaluate(
            """(sel) => Array.from(document.querySelectorAll(sel + ' tbody tr'))
                   .map(tr => Array.from(tr.cells).map(c => c.innerText.trim()))""",
            SEL_TARIFF_RESULT,
        )

        lines: list[TariffLine] = []
        for cells in raw:
            # 관찰된 레이아웃: [호, 소호, 세번, 한글품명, 영문품명, 기본세율, 탄력구분, 협정세율]
            if len(cells) < 5:
                continue
            cells = [c or None for c in cells]
            lines.append(
                TariffLine(
                    heading=cells[0] or heading_no,
                    sub_heading=(cells[1] or "") if len(cells) > 1 else "",
                    tariff_line=(cells[2] or "") if len(cells) > 2 else "",
                    name_kr=cells[3] if len(cells) > 3 else None,
                    name_en=cells[4] if len(cells) > 4 else None,
                    base_rate=cells[5] if len(cells) > 5 else None,
                    flex_rate_cls=cells[6] if len(cells) > 6 else None,
                    country_rate=cells[7] if len(cells) > 7 else None,
                )
            )

        self._append_manifest(
            {
                "kind": "tariff_schedule",
                "heading": heading_no,
                "url": page.url,
                "raw_html_path": str(html_path) if html_path else None,
                "rows": len(lines),
            }
        )

        self._last_request_at = time.monotonic()
        return lines

    def fetch_classification_cases(
        self,
        query: str,
        max_pages: int = 3,
        fetch_detail: bool = True,
        result_timeout_ms: int = 15_000,
    ) -> list[ClassificationCase]:
        """품목분류 사례 검색 (`openULS0203042S.do`).

        검색창에 품목명·HS 부호 등을 입력하면 사례 목록이 표로 출력된다.
        ``fetch_detail=True`` 인 경우 각 행을 클릭하여 결정 이유 등 상세 내용까지 수집.

        .. warning::
            셀렉터(``SEL_CASE_*``)는 잠정값. 최초 실행 시
            ``python -m scripts.dev_probe_cases --headed`` 로 실제 DOM 을 확인하고
            필요 시 상수를 갱신할 것. 리스트 컬럼 배치도 가정(사례번호/품명/HS/결정일)이며
            실제 사이트에 맞춰 ``_parse_case_row`` 매핑을 조정해야 한다.
        """
        if not query.strip():
            raise ValueError("query 가 비어있을 수 없습니다.")

        page = self._require_page()
        self._respect_rate_limit()

        page.goto(
            f"{self.base_url}{CLASSIFICATION_CASE_PATH}",
            wait_until="domcontentloaded",
        )
        try:
            page.wait_for_selector(SEL_CASE_INPUT, timeout=self.timeout_ms)
            page.fill(SEL_CASE_INPUT, query)
            page.click(SEL_CASE_SUBMIT)
            page.wait_for_selector(f"{SEL_CASE_RESULT} tbody tr", timeout=result_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError(f"품목분류 사례 검색 실패 (query={query!r})") from exc

        cases: list[ClassificationCase] = []
        for page_idx in range(max_pages):
            stem = f"case_search_{_slug(query)}_p{page_idx + 1}"
            html_path = self._persist_raw_html(subdir="cases", stem=stem)
            rows = page.evaluate(
                """(sel) => Array.from(document.querySelectorAll(sel + ' tbody tr'))
                       .map(tr => Array.from(tr.cells).map(c => c.innerText.trim()))""",
                SEL_CASE_RESULT,
            )
            for cells in rows:
                case = self._parse_case_row(cells, source_url=page.url)
                if case is None:
                    continue
                if fetch_detail:
                    try:
                        self._enrich_case_detail(case, row_cells=cells)
                    except PlaywrightTimeoutError:
                        logger.warning("사례 상세 펼침 실패 (case_ref=%s)", case.case_ref)
                cases.append(case)

            self._append_manifest(
                {
                    "kind": "classification_cases",
                    "query": query,
                    "page": page_idx + 1,
                    "url": page.url,
                    "raw_html_path": str(html_path) if html_path else None,
                    "rows": len(rows),
                }
            )

            if page_idx + 1 >= max_pages:
                break
            if not self._goto_next_case_page():
                break

        self._last_request_at = time.monotonic()
        return cases

    def _parse_case_row(self, cells: list[str], source_url: str) -> ClassificationCase | None:
        """사례 테이블 한 행을 ``ClassificationCase`` 로 매핑.

        잠정 가정: ``[사례번호, 품명, HS부호(10자리), 결정일, 요약]``.
        실측 후 cells 매핑을 수정하라.
        """
        cells = [c.strip() if isinstance(c, str) else "" for c in cells]
        non_empty = [c for c in cells if c]
        if not non_empty:
            return None
        case_ref = cells[0] if len(cells) > 0 else None
        product_name = cells[1] if len(cells) > 1 else non_empty[0]
        hs_code = cells[2] if len(cells) > 2 else None
        decision_date = cells[3] if len(cells) > 3 else None
        description = cells[4] if len(cells) > 4 else None
        if hs_code:
            hs_code = hs_code.replace("-", "").replace(".", "").replace(" ", "")
            if not (hs_code.isdigit() and len(hs_code) == 10):
                hs_code = None
        return ClassificationCase(
            case_ref=case_ref or None,
            product_name=product_name or "",
            hs_code=hs_code,
            decision_date=decision_date or None,
            description=description or None,
            source_url=source_url,
        )

    def _enrich_case_detail(self, case: ClassificationCase, row_cells: list[str]) -> None:
        """사례 상세 레이어 를 펼쳐 결정 이유 등 텍스트 추출."""
        page = self._require_page()
        # 행 클릭: 일반적으로 사례번호 링크
        ref = case.case_ref or (row_cells[0] if row_cells else "")
        if not ref:
            return
        link = page.query_selector(f'{SEL_CASE_DETAIL_LINK}:has-text("{ref}")')
        if link is None:
            # fallback: 첫 dtlInfo 링크
            link = page.query_selector(SEL_CASE_DETAIL_LINK)
        if link is None:
            return
        link.click()
        page.wait_for_selector(SEL_CASE_DETAIL_LAYER, timeout=self.timeout_ms)
        reasoning = self._text_or_none(SEL_CASE_DETAIL_LAYER)
        if reasoning:
            case.reasoning = reasoning
        # 닫기(다음 행 클릭 가능하도록). 실측 후 정확한 닫기 셀렉터로 교체.
        close_btn = page.query_selector(f"{SEL_CASE_DETAIL_LAYER} .btnClose")
        if close_btn:
            close_btn.click()

    def _goto_next_case_page(self) -> bool:
        """다음 페이지 링크가 있으면 클릭하고 True, 없으면 False."""
        page = self._require_page()
        next_link = page.query_selector(SEL_CASE_PAGINATION_NEXT)
        if next_link is None:
            return False
        try:
            next_link.click()
            page.wait_for_selector(f"{SEL_CASE_RESULT} tbody tr", timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            return False
        return True

    # ---- FAQ (openULS0206017Q) ----

    def fetch_faq(
        self,
        query: str,
        max_pages: int = 3,
        fetch_detail: bool = True,
        result_timeout_ms: int = 15_000,
    ) -> list[FAQEntry]:
        """CLIP FAQ 검색 (``openULS0206017Q.do``).

        분류/원산지/관세 등 Q&A 소스. 품목분류 보조 데이터로 활용.

        .. warning::
            셀렉터(``SEL_FAQ_*``)는 실측 전 잠정값. 최초 실행 시
            ``python -m scripts.dev_probe_faq --headed`` 로 실제 DOM 을 확인하고
            필요 시 상수 및 ``_parse_faq_row`` 매핑을 갱신할 것.
            컬럼 배치 가정: ``[FAQ번호, 분류, 질문요약, 등록일]``.
        """
        if not query.strip():
            raise ValueError("query 가 비어있을 수 없습니다.")

        page = self._require_page()
        self._respect_rate_limit()

        page.goto(
            f"{self.base_url}{FAQ_PATH}",
            wait_until="domcontentloaded",
        )
        try:
            page.wait_for_selector(SEL_FAQ_INPUT, timeout=self.timeout_ms)
            page.fill(SEL_FAQ_INPUT, query)
            page.click(SEL_FAQ_SUBMIT)
            page.wait_for_selector(f"{SEL_FAQ_RESULT} tbody tr", timeout=result_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ClipScrapeError(f"FAQ 검색 실패 (query={query!r})") from exc

        entries: list[FAQEntry] = []
        for page_idx in range(max_pages):
            stem = f"faq_search_{_slug(query)}_p{page_idx + 1}"
            html_path = self._persist_raw_html(subdir="faq", stem=stem)
            rows = page.evaluate(
                """(sel) => Array.from(document.querySelectorAll(sel + ' tbody tr'))
                       .map(tr => Array.from(tr.cells).map(c => c.innerText.trim()))""",
                SEL_FAQ_RESULT,
            )
            for cells in rows:
                entry = self._parse_faq_row(cells, source_url=page.url)
                if entry is None:
                    continue
                if fetch_detail:
                    try:
                        self._enrich_faq_detail(entry, row_cells=cells)
                    except PlaywrightTimeoutError:
                        logger.warning("FAQ 상세 펼침 실패 (faq_id=%s)", entry.faq_id)
                entries.append(entry)

            self._append_manifest(
                {
                    "kind": "faq",
                    "query": query,
                    "page": page_idx + 1,
                    "url": page.url,
                    "raw_html_path": str(html_path) if html_path else None,
                    "rows": len(rows),
                }
            )

            if page_idx + 1 >= max_pages:
                break
            if not self._goto_next_faq_page():
                break

        self._last_request_at = time.monotonic()
        return entries

    def _parse_faq_row(self, cells: list[str], source_url: str) -> FAQEntry | None:
        """FAQ 테이블 한 행을 ``FAQEntry`` 로 매핑.

        잠정 가정: ``[FAQ번호, 분류, 질문요약, 등록일]``.
        실측 후 cells 매핑을 수정하라.
        """
        cells = [c.strip() if isinstance(c, str) else "" for c in cells]
        non_empty = [c for c in cells if c]
        if not non_empty:
            return None
        faq_id = cells[0] if len(cells) > 0 else None
        category = cells[1] if len(cells) > 1 else None
        question = cells[2] if len(cells) > 2 else non_empty[0]
        decision_date = cells[3] if len(cells) > 3 else None
        return FAQEntry(
            faq_id=faq_id or None,
            question=question or "",
            category=category or None,
            decision_date=decision_date or None,
            source_url=source_url,
        )

    def _enrich_faq_detail(self, entry: FAQEntry, row_cells: list[str]) -> None:
        """FAQ 상세 레이어 펼쳐 답변 텍스트 추출."""
        page = self._require_page()
        ref = entry.faq_id or (row_cells[0] if row_cells else "")
        if not ref:
            return
        link = page.query_selector(f'{SEL_FAQ_DETAIL_LINK}:has-text("{ref}")')
        if link is None:
            link = page.query_selector(SEL_FAQ_DETAIL_LINK)
        if link is None:
            return
        link.click()
        page.wait_for_selector(SEL_FAQ_DETAIL_LAYER, timeout=self.timeout_ms)
        answer = self._text_or_none(SEL_FAQ_DETAIL_LAYER)
        if answer:
            entry.answer = answer

    def _goto_next_faq_page(self) -> bool:
        page = self._require_page()
        next_link = page.query_selector(SEL_FAQ_PAGINATION_NEXT)
        if next_link is None:
            return False
        try:
            next_link.click()
            page.wait_for_selector(f"{SEL_FAQ_RESULT} tbody tr", timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            return False
        return True

    # ---- HSK 버전 감지 ----

    def list_available_hsk_years(self) -> list[int]:
        """해설서 페이지의 HSK 연도 드롭다운에서 선택 가능한 연도 목록.

        5년 주기 HS 개정 감지에 사용 — 새 연도가 등장하면 재임베딩 파이프라인을
        가동해야 한다 (docs/hsk-version-migration.md 참조).

        :returns: 오름차순 정렬된 연도 리스트. 페이지 로딩 실패 시 빈 리스트.
        """
        page = self._require_page()
        self._respect_rate_limit()
        try:
            page.goto(f"{self.base_url}{HS_MANUAL_PATH}", wait_until="domcontentloaded")
            page.wait_for_selector(SEL_YEAR_SELECT, timeout=self.timeout_ms)
        except PlaywrightTimeoutError:
            logger.warning("HSK 연도 드롭다운 로딩 실패")
            return []
        # 드롭다운 option value 들을 JS 로 일괄 수집.
        values = page.evaluate(
            """(sel) => Array.from(document.querySelectorAll(sel + ' option'))
                   .map(o => (o.value || o.textContent || '').trim())""",
            SEL_YEAR_SELECT,
        )
        self._last_request_at = time.monotonic()
        return _parse_year_options(values)

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
            raise ClipScrapeError("ClipScraper 는 컨텍스트 매니저(`with ...`)로 사용해야 합니다.")
        return self._page

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)

    # ---- 재시도 (지수 백오프) ----

    # 재시도 대상 예외: Playwright Timeout 및 기타 네트워크 transient.
    # ``ClipScrapeError`` 는 우리가 의도적으로 올리는 업무 오류라 재시도 대상 제외.
    _TRANSIENT_EXC: tuple[type[BaseException], ...] = (PlaywrightTimeoutError,)

    def _retry_transient(
        self,
        fn,
        *,
        kind: str,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        context: dict[str, Any] | None = None,
    ):
        """``fn()`` 을 지수 백오프 재시도. transient 실패에만 적용.

        :param kind: 로깅·manifest 식별자 (예: ``explanatory_note``).
        :param context: manifest 에 실릴 부가 정보 (heading/year 등).
        :raises: 최종 시도도 실패하면 마지막 예외를 그대로 재발생.
        """
        last_exc: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except self._TRANSIENT_EXC as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    break
                wait = base_delay ** (attempt - 1) * self.rate_limit_sec
                logger.warning(
                    "[%s] transient 실패 attempt=%d/%d: %s (sleep %.1fs)",
                    kind,
                    attempt,
                    max_attempts,
                    exc,
                    wait,
                )
                time.sleep(wait)
        # 모든 재시도 소진 → 실패 매니페스트 + 재발생
        self._append_manifest(
            {
                "event": "failure",
                "kind": kind,
                "error_type": type(last_exc).__name__ if last_exc else "Unknown",
                "error": str(last_exc)[:300] if last_exc else "",
                "attempts": max_attempts,
                **(context or {}),
            }
        )
        assert last_exc is not None  # for mypy
        raise last_exc

    def _persist_raw_html(self, subdir: str, stem: str) -> Path | None:
        """raw_html_dir 가 설정된 경우 현재 페이지 HTML 을 저장하고 경로 반환."""
        if self.raw_html_dir is None:
            return None
        page = self._require_page()
        target_dir = self.raw_html_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stem}.html"
        try:
            path.write_text(page.content(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logger.exception("raw HTML 저장 실패: %s", path)
            return None
        return path

    def _append_manifest(self, record: dict[str, Any]) -> None:
        """manifest_path 가 설정된 경우 수집 메타 한 줄(jsonl) append.

        ``record`` 에 ``event`` 키가 없으면 ``"success"`` 로 기본 채움 (하위 호환).
        실패 기록은 ``event: "failure"`` + ``error``/``error_type``/``attempts`` 포함.
        """
        if self.manifest_path is None:
            return
        enriched = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "event": record.get("event", "success"),
            **record,
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.manifest_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(enriched, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            logger.exception("manifest.jsonl 기록 실패: %s", self.manifest_path)


# ---- 모듈 수준 헬퍼 ----


def _parse_year_options(raw: list[str]) -> list[int]:
    """drop-down option 문자열 목록 → 유효한 4자리 연도 정수 오름차순.

    ``ClipScraper.list_available_hsk_years`` 와 테스트 양쪽에서 사용.
    """
    years: set[int] = set()
    for v in raw:
        digits = "".join(ch for ch in (v or "") if ch.isdigit())
        if len(digits) < 4:
            continue
        try:
            y = int(digits[:4])
        except ValueError:
            continue
        if 1990 <= y <= 2100:
            years.add(y)
    return sorted(years)
