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

    def search_hs_sgn(
        self,
        hs_code: str,
        service_name: str = "hsSgnQry",
        operation: str = "searchHsSgn",
    ) -> dict[str, str | None] | None:
        """UNIPASS **HS 부호 조회** (API018, ``hsSgnQry/searchHsSgn``).

        공식 엔드포인트이며 Base URL·서비스명은 관세청 공개 레퍼런스로 확인됨.
        반면 응답 XML 태그명(``korItemNm``, ``engItemNm`` 등)은 **로그인 전용**
        연계가이드 PDF 에만 존재하여 본 구현에서는 **미검증 추정치**이다.
        최초 호출 시 ``call()`` 로 루트 Element 를 받아 실제 태그를 확인한 뒤
        이 메서드의 파싱 부분을 조정할 것.

        UNIPASS API 는 **관세율표(trrf)** 조회를 직접 제공하지 않는다.
        관세율이 필요하면 CLIP 스크래퍼의 관세율표 페이지를 사용해야 한다.

        :param hs_code: 10자리 HS 부호(예: ``"8471300000"``).
        :returns: 첫 번째 결과 row dict, 결과 없으면 ``None``.
        """
        root = self.call(
            service_name=service_name,
            operation=operation,
            params={"hsSgn": hs_code},
        )
        item = root.find(".//*[hsSgn]")
        if item is None:
            return None
        # 아래 태그명은 미검증. 실응답 확인 후 교체.
        return {
            "hs_code": item.findtext("hsSgn"),
            "name_kr": item.findtext("korItemNm"),
            "name_en": item.findtext("engItemNm"),
        }


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
