"""CLIP(관세법령정보포털) 해설서·주(Note) 규정 수집 스크래퍼.

CLIP 페이지 구조는 동적 렌더링이 섞여 있어, 현재 모듈은 정적 HTML 응답을
대상으로 한 골격만 제공한다. 실제 운영 적용 전 다음을 반드시 점검할 것.

1. ``robots.txt`` 및 이용약관에서 자동 수집 허용 여부 확인.
2. 대상 페이지가 JS 렌더링이면 ``requests`` 대신 Playwright/Selenium 으로 교체.
3. 호(Heading) → 류(Chapter) → 부(Section) URL 패턴을 실제 응답 기준으로 검증.
4. 셀렉터(``_SELECTORS``) 는 실제 DOM 구조에 맞게 갱신.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://unipass.customs.go.kr/clip"
DEFAULT_USER_AGENT = "CustomsAIAgent/0.1 (+contact: ops@example.com)"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RATE_LIMIT_SEC = 1.0

# 실제 CLIP DOM 점검 후 수정 필요. 잘못된 셀렉터 사용 시 빈 결과 반환.
_SELECTORS = {
    "section_note": "div.section-note",
    "chapter_note": "div.chapter-note",
    "heading_body": "div.heading-content",
}


class ClipScrapeError(RuntimeError):
    """CLIP 응답 파싱 또는 HTTP 실패."""


@dataclass
class ExplanatoryNote:
    """수집된 해설서 단위 데이터."""

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


@dataclass
class ClipScraper:
    """CLIP 해설서 스크래퍼.

    `fetch_explanatory_note` 는 단일 호(Heading) 페이지를 가져와
    부/류 주 규정과 본문 해설을 분리해 반환한다.
    """

    base_url: str = DEFAULT_BASE_URL
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = DEFAULT_TIMEOUT
    rate_limit_sec: float = DEFAULT_RATE_LIMIT_SEC
    hsk_version: str = "2022"
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        self.session = self.session or requests.Session()
        self.session.headers.setdefault("User-Agent", self.user_agent)
        self._last_request_at: float = 0.0

    def fetch_explanatory_note(self, heading_no: str) -> ExplanatoryNote:
        """4자리 호(Heading) 번호 기준 해설서 수집."""
        html = self._get(self._heading_url(heading_no))
        soup = BeautifulSoup(html, "html.parser")
        return ExplanatoryNote(
            heading=heading_no,
            section_note=self._extract_text(soup, _SELECTORS["section_note"]),
            chapter_note=self._extract_text(soup, _SELECTORS["chapter_note"]),
            content=self._extract_text(soup, _SELECTORS["heading_body"]),
            metadata={
                "source": "CLIP",
                "hsk_version": self.hsk_version,
                "url": self._heading_url(heading_no),
            },
        )

    def fetch_many(self, heading_nos: Iterable[str]) -> list[ExplanatoryNote]:
        """여러 호를 순차 수집. 페이지당 ``rate_limit_sec`` 만큼 간격."""
        return [self.fetch_explanatory_note(h) for h in heading_nos]

    def _heading_url(self, heading_no: str) -> str:
        # 실제 CLIP URL 패턴으로 교체 필요.
        return f"{self.base_url}/heading/{heading_no}"

    def _get(self, url: str) -> str:
        self._respect_rate_limit()
        logger.debug("CLIP GET %s", url)
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ClipScrapeError(f"CLIP 요청 실패: {url}") from exc
        finally:
            self._last_request_at = time.monotonic()
        return response.text

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)

    @staticmethod
    def _extract_text(soup: BeautifulSoup, selector: str) -> str | None:
        node = soup.select_one(selector)
        if node is None:
            return None
        text = node.get_text(separator="\n", strip=True)
        return text or None
