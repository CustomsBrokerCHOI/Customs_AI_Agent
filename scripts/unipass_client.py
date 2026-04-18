"""UNIPASS(관세청 전자통관시스템) Open API 기본 클라이언트.

사용 예::

    from scripts.unipass_client import UnipassClient

    client = UnipassClient()
    root = client.call(
        service_name="retrieveTrrfCd",
        operation="retrieveTrrfCd",
        params={"hsSgn": "8471300000"},
    )
    for item in root.findall(".//tariff"):
        print(item.findtext("hsSgn"), item.findtext("trrfCd"))
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://unipass.customs.go.kr:38010/ext/rest"
DEFAULT_TIMEOUT = 10.0


class UnipassError(RuntimeError):
    """UNIPASS 호출 실패 또는 업무 오류 응답."""


@dataclass
class UnipassClient:
    """UNIPASS Open API 호출을 캡슐화한 얇은 래퍼.

    인증키는 `api_key` 인자 또는 `UNIPASS_API_KEY` 환경변수에서 읽는다.
    응답은 기본적으로 XML 이며 `xml.etree.ElementTree.Element` 로 반환한다.
    """

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("UNIPASS_API_KEY")
        if not self.api_key:
            raise UnipassError(
                "UNIPASS_API_KEY 가 설정되지 않았습니다. 환경변수 또는 api_key 인자를 지정하세요."
            )
        self.session = self.session or requests.Session()

    def call(
        self,
        service_name: str,
        operation: str,
        params: Mapping[str, Any] | None = None,
    ) -> ET.Element:
        """단일 UNIPASS 서비스 호출.

        :param service_name: URL 경로에 포함되는 서비스명(예: ``retrieveTrrfCd``)
        :param operation: URL 경로의 오퍼레이션명. 대부분 ``service_name`` 과 동일.
        :param params: 쿼리 파라미터. 인증키(``crkyCn``)는 자동 주입된다.
        :returns: 루트 ``Element``.
        :raises UnipassError: HTTP 실패 또는 응답에 업무 오류 코드가 포함된 경우.
        """
        url = f"{self.base_url}/{service_name}/{operation}"
        query: dict[str, Any] = {"crkyCn": self.api_key}
        if params:
            query.update(params)

        logger.debug("UNIPASS 호출 %s params=%s", url, {k: v for k, v in query.items() if k != "crkyCn"})
        response = self.session.get(url, params=query, timeout=self.timeout)
        response.raise_for_status()

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise UnipassError(f"응답 XML 파싱 실패: {exc}") from exc

        error_code = root.findtext(".//ntceInfo") or root.findtext(".//errYn")
        if error_code and error_code.strip().upper() == "Y":
            message = root.findtext(".//ntceInfo") or "알 수 없는 UNIPASS 오류"
            raise UnipassError(message)

        return root

    def get_hs_code_info(
        self,
        hs_code: str,
        service_name: str = "retrieveTrrfCd",
    ) -> dict[str, str | None] | None:
        """HS 코드 기준 관세율·품목명 조회.

        실제 서비스/태그명은 UNIPASS 명세서 버전에 따라 다를 수 있으므로,
        호출 후 반환되는 ``Element`` 구조를 한 번 검증한 뒤 운영 환경에 적용한다.

        :param hs_code: 10자리 HS 부호(예: ``"8471300000"``).
        :param service_name: UNIPASS 서비스명. 기본값은 관세율 조회 서비스.
        :returns: 첫 번째 결과 row를 dict 로 변환. 결과가 없으면 ``None``.
        """
        root = self.call(
            service_name=service_name,
            operation=service_name,
            params={"hsSgn": hs_code},
        )
        item = root.find(".//hsSgn/..") or root.find(".//*[hsSgn]")
        if item is None:
            return None
        return {
            "hs_code": item.findtext("hsSgn"),
            "name_kr": item.findtext("korItemNm"),
            "name_en": item.findtext("engItemNm"),
            "base_rate": item.findtext("trrfCd") or item.findtext("totTaxRt"),
        }

    def get_classification_cases(
        self,
        item_name: str,
        service_name: str = "retrieveCsClCdInfo",
    ) -> list[dict[str, str | None]]:
        """품목분류 사례 조회.

        :param item_name: 검색할 품목명.
        :param service_name: UNIPASS 서비스명. 명세서로 확인 후 조정.
        :returns: 사례 row 리스트. 없으면 빈 리스트.
        """
        root = self.call(
            service_name=service_name,
            operation=service_name,
            params={"itemNm": item_name},
        )
        cases: list[dict[str, str | None]] = []
        for node in root.findall(".//*[hsSgn]"):
            cases.append(
                {
                    "hs_code": node.findtext("hsSgn"),
                    "item_name": node.findtext("itemNm") or node.findtext("korItemNm"),
                    "decision_date": node.findtext("dcsnDt"),
                    "summary": node.findtext("dcsnSumry") or node.findtext("dscrCn"),
                }
            )
        return cases


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


if __name__ == "__main__":
    import argparse

    _configure_logging()
    parser = argparse.ArgumentParser(description="UNIPASS API 호출 도우미")
    parser.add_argument("service", help="서비스명(예: retrieveTrrfCd)")
    parser.add_argument("--operation", help="오퍼레이션명. 생략 시 service 와 동일", default=None)
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="쿼리 파라미터. 여러 번 지정 가능",
    )
    args = parser.parse_args()

    parsed_params: dict[str, str] = {}
    for raw in args.param:
        if "=" not in raw:
            parser.error(f"--param 값은 KEY=VALUE 형식이어야 합니다: {raw}")
        key, value = raw.split("=", 1)
        parsed_params[key] = value

    client = UnipassClient()
    root = client.call(
        service_name=args.service,
        operation=args.operation or args.service,
        params=parsed_params,
    )
    print(ET.tostring(root, encoding="unicode"))
